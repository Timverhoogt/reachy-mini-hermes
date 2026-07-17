#!/usr/bin/env python3
"""Authenticated voice companion for Reachy Mini and Hermes Agent.

Run this with the Python environment that runs Hermes Agent. It reuses the
profile's configured STT/TTS providers and forwards chat to Hermes' official
OpenAI-compatible API server.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import mimetypes
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

_LOGGER = logging.getLogger("hermes_reachy_bridge")
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_MAX_TTS_CHARACTERS = 15_000


def _hermes_home(profile: str | None = None) -> Path:
    root = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
    return root / "profiles" / profile if profile else root


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _resolve_api_key(explicit: str, profile: str | None) -> str:
    if explicit:
        return explicit
    environment = os.getenv("API_SERVER_KEY", "").strip()
    if environment:
        return environment
    env_value = _parse_env_file(_hermes_home(profile) / ".env").get("API_SERVER_KEY", "").strip()
    if env_value:
        return env_value

    # Current Hermes releases persist `hermes config set` values in the
    # profile's config.yaml. This script runs in Hermes' venv, where PyYAML is
    # already installed, so users do not need to duplicate the key in .env.
    try:
        import yaml

        config_path = _hermes_home(profile) / "config.yaml"
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if isinstance(payload, dict):
            return str(payload.get("API_SERVER_KEY") or "").strip()
    except (ImportError, OSError, ValueError, TypeError):
        pass
    return ""


def _ensure_hermes_imports() -> None:
    candidates = [
        Path(os.getenv("HERMES_AGENT_DIR", "")).expanduser() if os.getenv("HERMES_AGENT_DIR") else None,
        Path.home() / ".hermes" / "hermes-agent",
        Path.home() / ".hermes-agent",
    ]
    for candidate in candidates:
        if candidate and (candidate / "tools" / "transcription_tools.py").exists():
            sys.path.insert(0, str(candidate))
            return
    raise RuntimeError(
        "Could not locate the Hermes Agent source. Set HERMES_AGENT_DIR to the Hermes install directory."
    )


class Bridge:
    def __init__(self, *, api_key: str, hermes_url: str, profile: str | None = None) -> None:
        self.api_key = api_key
        self.hermes_url = hermes_url.rstrip("/")
        self.profile = profile
        self.http: ClientSession | None = None

    async def start(self, app: web.Application) -> None:
        self.http = ClientSession(timeout=ClientTimeout(total=180, connect=10))

    async def stop(self, app: web.Application) -> None:
        if self.http is not None:
            await self.http.close()

    def require_auth(self, request: web.Request) -> None:
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {self.api_key}"
        if not self.api_key or not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": {"message": "Invalid API key", "type": "authentication_error"}}),
                content_type="application/json",
            )

    async def health(self, request: web.Request) -> web.Response:
        hermes_ok = False
        if self.http is not None:
            try:
                async with self.http.get(f"{self.hermes_url}/health") as response:
                    hermes_ok = response.status == 200
            except Exception:
                hermes_ok = False
        return web.json_response({"status": "ok" if hermes_ok else "degraded", "hermes_api": hermes_ok})

    async def chat(self, request: web.Request) -> web.Response:
        self.require_auth(request)
        if self.http is None:
            raise web.HTTPServiceUnavailable(text="Bridge HTTP client is not ready")
        try:
            payload = await request.json()
        except Exception as exc:
            raise web.HTTPBadRequest(text="Invalid JSON") from exc
        if payload.get("stream"):
            raise web.HTTPBadRequest(text="The Reachy bridge currently requires stream=false")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        for name in ("X-Hermes-Session-Id", "X-Hermes-Session-Key", "Idempotency-Key"):
            if value := request.headers.get(name):
                headers[name] = value
        async with self.http.post(f"{self.hermes_url}/v1/chat/completions", json=payload, headers=headers) as upstream:
            body = await upstream.read()
            response_headers = {}
            for name in ("X-Hermes-Session-Id", "X-Hermes-Session-Key"):
                if value := upstream.headers.get(name):
                    response_headers[name] = value
            return web.Response(
                status=upstream.status,
                body=body,
                content_type=upstream.content_type or "application/json",
                headers=response_headers,
            )

    async def transcribe(self, request: web.Request) -> web.Response:
        self.require_auth(request)
        if not request.content_type.startswith("multipart/"):
            raise web.HTTPBadRequest(text="Expected multipart form data")
        reader = await request.multipart()
        temp_path = ""
        try:
            while part := await reader.next():
                if part.name != "file":
                    await part.release()
                    continue
                suffix = Path(part.filename or "audio.wav").suffix or ".wav"
                with tempfile.NamedTemporaryFile(prefix="reachy-hermes-stt-", suffix=suffix, delete=False) as output:
                    temp_path = output.name
                    total = 0
                    while chunk := await part.read_chunk(64 * 1024):
                        total += len(chunk)
                        if total > _MAX_AUDIO_BYTES:
                            raise web.HTTPRequestEntityTooLarge(max_size=_MAX_AUDIO_BYTES, actual_size=total)
                        output.write(chunk)
                break
            if not temp_path:
                raise web.HTTPBadRequest(text="Missing audio file")

            _ensure_hermes_imports()
            from tools.transcription_tools import transcribe_audio

            result = await asyncio.to_thread(transcribe_audio, temp_path)
            if not result.get("success"):
                raise web.HTTPBadRequest(text=str(result.get("error") or "Transcription failed"))
            return web.json_response(
                {
                    "text": str(result.get("transcript") or "").strip(),
                    "provider": result.get("provider"),
                }
            )
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    async def speech(self, request: web.Request) -> web.Response:
        self.require_auth(request)
        try:
            payload = await request.json()
            text = str(payload.get("input") or payload.get("text") or "").strip()
        except Exception as exc:
            raise web.HTTPBadRequest(text="Invalid JSON") from exc
        if not text:
            raise web.HTTPBadRequest(text="Missing input text")
        if len(text) > _MAX_TTS_CHARACTERS:
            raise web.HTTPRequestEntityTooLarge(max_size=_MAX_TTS_CHARACTERS, actual_size=len(text))

        _ensure_hermes_imports()
        from tools.tts_tool import text_to_speech_tool

        temp_directory = Path(tempfile.mkdtemp(prefix="reachy-hermes-tts-"))
        requested_path = temp_directory / "speech.mp3"
        try:
            raw_result = await asyncio.to_thread(text_to_speech_tool, text, str(requested_path))
            result: dict[str, Any] = json.loads(raw_result)
            if not result.get("success"):
                raise web.HTTPBadRequest(text=str(result.get("error") or "Speech synthesis failed"))
            actual_path = Path(str(result.get("file_path") or requested_path))
            audio = actual_path.read_bytes()
            content_type = mimetypes.guess_type(actual_path.name)[0] or "application/octet-stream"
            return web.Response(
                body=audio,
                content_type=content_type,
                headers={"Content-Disposition": f'inline; filename="{actual_path.name}"'},
            )
        finally:
            for child in temp_directory.glob("*"):
                try:
                    child.unlink()
                except OSError:
                    pass
            try:
                temp_directory.rmdir()
            except OSError:
                pass


def create_app(*, api_key: str, hermes_url: str, profile: str | None = None) -> web.Application:
    bridge = Bridge(api_key=api_key, hermes_url=hermes_url, profile=profile)
    app = web.Application(client_max_size=_MAX_AUDIO_BYTES + 1024 * 1024)
    app.on_startup.append(bridge.start)
    app.on_cleanup.append(bridge.stop)
    app.router.add_get("/health", bridge.health)
    app.router.add_post("/v1/chat/completions", bridge.chat)
    app.router.add_post("/v1/audio/transcriptions", bridge.transcribe)
    app.router.add_post("/v1/audio/speech", bridge.speech)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice bridge between Reachy Mini and Hermes Agent")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host; use 0.0.0.0 only on a trusted LAN/VPN")
    parser.add_argument("--port", type=int, default=8643)
    parser.add_argument("--hermes-url", default="http://127.0.0.1:8642")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    api_key = _resolve_api_key(args.api_key, args.profile)
    if not api_key:
        parser.error("No API key found. Pass --api-key or configure API_SERVER_KEY in the Hermes profile .env")
    web.run_app(
        create_app(api_key=api_key, hermes_url=args.hermes_url, profile=args.profile), host=args.host, port=args.port
    )


if __name__ == "__main__":
    main()
