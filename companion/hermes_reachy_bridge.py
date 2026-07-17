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

from aiohttp import ClientSession, ClientTimeout, FormData, web

_LOGGER = logging.getLogger("hermes_reachy_bridge")
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_MAX_TTS_CHARACTERS = 15_000
_MAX_REALTIME_MESSAGE_BYTES = 2 * 1024 * 1024


def _build_realtime_tools(camera_enabled: bool, robot_tools_enabled: bool) -> list[dict[str, Any]]:
    """Build the curated Realtime tool surface without exposing privileged credentials."""
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "name": "ask_hermes",
            "description": "Use Hermes memory and tools to answer or perform the request.",
            "parameters": {
                "type": "object",
                "properties": {"request": {"type": "string"}},
                "required": ["request"],
                "additionalProperties": False,
            },
        }
    ]
    if camera_enabled:
        tools.append(
            {
                "type": "function",
                "name": "capture_reachy_camera",
                "description": (
                    "Capture exactly one current still image from Reachy's camera. Call only when the user "
                    "explicitly asks you to look, see, read, identify, inspect, or otherwise answer from "
                    "the robot's current view. Never call for monitoring or speculatively."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "purpose": {
                            "type": "string",
                            "description": "Short reason the current camera frame is needed.",
                        }
                    },
                    "required": ["purpose"],
                    "additionalProperties": False,
                },
            }
        )
    if robot_tools_enabled:
        tools.extend(
            [
                {
                    "type": "function",
                    "name": "move_reachy_head",
                    "description": (
                        "Physically look left, right, up, down, or return to center. Use when the user asks "
                        "Reachy to look in a direction, or when one subtle physical gesture adds meaning."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "direction": {
                                "type": "string",
                                "enum": ["left", "right", "up", "down", "center"],
                            }
                        },
                        "required": ["direction"],
                        "additionalProperties": False,
                    },
                },
                {
                    "type": "function",
                    "name": "express_reachy_emotion",
                    "description": (
                        "Express one concise emotion using Reachy's authentic recorded head and antenna motion. "
                        "Use sparingly when requested or when it naturally strengthens the interaction."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "emotion": {
                                "type": "string",
                                "enum": [
                                    "happy",
                                    "excited",
                                    "loving",
                                    "grateful",
                                    "thinking",
                                    "confused",
                                    "sad",
                                    "surprised",
                                    "calm",
                                    "welcoming",
                                    "yes",
                                    "no",
                                ],
                            }
                        },
                        "required": ["emotion"],
                        "additionalProperties": False,
                    },
                },
                {
                    "type": "function",
                    "name": "dance_reachy",
                    "description": (
                        "Perform one authentic Reachy dance. Use only when the user asks for a dance or celebration; "
                        "prefer short unless a longer style is explicitly wanted."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "style": {
                                "type": "string",
                                "enum": ["short", "groovy", "energetic"],
                            }
                        },
                        "required": ["style"],
                        "additionalProperties": False,
                    },
                },
            ]
        )
    return tools


def _completed_hermes_call(
    kind: str,
    event: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Parse ask_hermes only after OpenAI marks the function-call item completed."""
    if kind != "response.output_item.done":
        return None
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    call_id = str(item.get("call_id") or "")
    if (
        item.get("type") != "function_call"
        or item.get("status") != "completed"
        or item.get("name") != "ask_hermes"
        or not call_id
    ):
        return None
    try:
        arguments = json.loads(item.get("arguments") or "{}")
    except (TypeError, json.JSONDecodeError):
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return call_id, arguments


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


def _resolve_secret(name: str, profile: str | None) -> str:
    environment = os.getenv(name, "").strip()
    if environment:
        return environment
    return _parse_env_file(_hermes_home(profile) / ".env").get(name, "").strip()


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
        providers: dict[str, str] = {}
        try:
            import yaml

            payload = yaml.safe_load(
                (_hermes_home(self.profile) / "config.yaml").read_text(encoding="utf-8")
            ) or {}
            for section in ("stt", "tts"):
                value = payload.get(section, {})
                if isinstance(value, dict) and value.get("provider"):
                    providers[f"{section}_provider"] = str(value["provider"])
        except (ImportError, OSError, TypeError, ValueError):
            pass
        return web.json_response(
            {
                "status": "ok" if hermes_ok else "degraded",
                "hermes_api": hermes_ok,
                "realtime_available": bool(_resolve_secret("OPENAI_API_KEY", self.profile)),
                "realtime_model": "gpt-realtime-2.1",
                **providers,
            }
        )

    async def models(self, request: web.Request) -> web.Response:
        """Expose only the model aliases configured by Hermes API Server."""
        self.require_auth(request)
        if self.http is None:
            raise web.HTTPServiceUnavailable(text="Bridge HTTP client is not ready")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with self.http.get(f"{self.hermes_url}/v1/models", headers=headers) as upstream:
            body = await upstream.read()
            return web.Response(
                status=upstream.status,
                body=body,
                content_type=upstream.content_type or "application/json",
            )

    async def voice_options(self, request: web.Request) -> web.Response:
        """Return credential-backed speech options without exposing credentials."""
        self.require_auth(request)
        if self.http is None:
            raise web.HTTPServiceUnavailable(text="Bridge HTTP client is not ready")
        eleven_key = _resolve_secret("ELEVENLABS_API_KEY", self.profile)
        options: dict[str, object] = {
            "stt": [
                {"id": "configured", "label": "Hermes configured STT"},
                {"id": "local", "label": "Local Whisper", "models": ["base"]},
            ],
            "tts": [
                {"id": "configured", "label": "Hermes configured TTS"},
            ],
        }
        if eleven_key:
            voices: list[dict[str, str]] = []
            try:
                async with self.http.get(
                    "https://api.elevenlabs.io/v1/voices",
                    headers={"xi-api-key": eleven_key},
                ) as upstream:
                    if upstream.status == 200:
                        payload = await upstream.json()
                        voices = [
                            {
                                "id": str(item.get("voice_id") or ""),
                                "name": str(item.get("name") or "Unnamed voice"),
                            }
                            for item in payload.get("voices", [])
                            if item.get("voice_id")
                        ]
            except Exception:
                _LOGGER.warning("Could not list ElevenLabs voices", exc_info=True)
            options["stt"].append(  # type: ignore[union-attr]
                {"id": "elevenlabs", "label": "ElevenLabs Scribe API", "models": ["scribe_v2"]}
            )
            options["tts"].append(  # type: ignore[union-attr]
                {
                    "id": "elevenlabs",
                    "label": "ElevenLabs API",
                    "models": ["eleven_flash_v2_5", "eleven_multilingual_v2"],
                    "voices": voices,
                }
            )
        return web.json_response(options)

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

    async def _hermes_answer(
        self,
        text: str,
        *,
        model: str,
        system_prompt: str,
        session_id: str,
    ) -> str:
        if self.http is None:
            raise RuntimeError("Bridge HTTP client is not ready")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Hermes-Session-Id": session_id,
        }
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        }
        async with self.http.post(
            f"{self.hermes_url}/v1/chat/completions", json=payload, headers=headers
        ) as response:
            body = await response.json(content_type=None)
            if response.status != 200:
                raise RuntimeError(str(body.get("error") or body))
            return str(body["choices"][0]["message"]["content"]).strip()

    async def realtime(self, request: web.Request) -> web.StreamResponse:
        """Proxy a private Reachy audio session to OpenAI Realtime.

        The OpenAI credential never leaves the Hermes host. Reachy sends and
        receives standard Realtime events through this authenticated LAN socket.
        A curated ``ask_hermes`` tool keeps personal memory and consequential
        actions authoritative in Hermes rather than duplicating them in a voice
        model prompt.
        """
        self.require_auth(request)
        openai_key = _resolve_secret("OPENAI_API_KEY", self.profile)
        if not openai_key:
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": {"message": "OPENAI_API_KEY is not configured on the Hermes host"}}),
                content_type="application/json",
            )

        client = web.WebSocketResponse(heartbeat=20, max_msg_size=_MAX_REALTIME_MESSAGE_BYTES)
        await client.prepare(request)
        first = await client.receive(timeout=15)
        if first.type != web.WSMsgType.TEXT:
            await client.close(code=1002, message=b"session.start required")
            return client
        try:
            config = json.loads(first.data)
            if config.get("type") != "session.start":
                raise ValueError("session.start required")
        except (TypeError, ValueError, json.JSONDecodeError):
            await client.close(code=1002, message=b"invalid session.start")
            return client

        model = str(config.get("model") or "gpt-realtime-2.1")[:80]
        voice = str(config.get("voice") or "marin")[:40]
        reasoning_effort = str(config.get("reasoning_effort") or "low")
        if reasoning_effort not in {"minimal", "low", "medium", "high", "xhigh"}:
            reasoning_effort = "low"
        agent_model = str(config.get("agent_model") or "hermes-agent")[:160]
        session_id = str(config.get("session_id") or "reachy-realtime")[:160]
        system_prompt = str(config.get("system_prompt") or "")[:8_000]
        camera_enabled = config.get("camera_enabled") is True
        robot_tools_enabled = config.get("robot_tools_enabled") is True
        realtime_tools = _build_realtime_tools(camera_enabled, robot_tools_enabled)
        camera_instruction = (
            "The camera is still-image-only. When the user explicitly asks you to look, see, read, identify, "
            "inspect, or answer from Reachy's current view, call capture_reachy_camera before answering and "
            "describe only that fresh frame. Never capture speculatively, repeatedly, or for monitoring. "
            if camera_enabled
            else "Do not claim to see the physical environment because camera access is disabled. "
        )
        robot_instruction = (
            "You can physically embody responses with move_reachy_head, express_reachy_emotion, and dance_reachy. "
            "Use them when the user asks, or sparingly when one subtle gesture adds meaning. Never overact, never "
            "chain dances, and prefer the short dance unless the user explicitly asks for a longer performance. "
            if robot_tools_enabled
            else "Do not claim to perform physical robot actions because robot tools are disabled. "
        )
        instructions = (
            "You are Hermes, speaking through a Reachy Mini robot. Be concise, natural, and conversational. "
            "Never say punctuation names or announce that you are awake. You may answer simple social conversation "
            "directly. For personal memory, current information, Home Assistant, files, devices, or any consequential "
            "action, call ask_hermes and faithfully speak its result. Never claim an action "
            "succeeded without that tool. "
            + camera_instruction
            + robot_instruction
            + system_prompt
        )
        upstream_headers = {"Authorization": f"Bearer {openai_key}"}
        upstream_url = f"wss://api.openai.com/v1/realtime?model={model}"
        ws_timeout = ClientTimeout(total=None, connect=10, sock_connect=10)

        try:
            async with ClientSession(timeout=ws_timeout) as realtime_http:
                async with realtime_http.ws_connect(
                    upstream_url,
                    headers=upstream_headers,
                    heartbeat=20,
                    max_msg_size=_MAX_REALTIME_MESSAGE_BYTES,
                ) as upstream:
                    await upstream.send_json(
                        {
                            "type": "session.update",
                            "session": {
                                "type": "realtime",
                                "model": model,
                                "instructions": instructions,
                                "output_modalities": ["audio"],
                                "reasoning": {"effort": reasoning_effort},
                                "audio": {
                                    "input": {
                                        "format": {"type": "audio/pcm", "rate": 24000},
                                        "turn_detection": {
                                            "type": "semantic_vad",
                                            "create_response": True,
                                            "interrupt_response": True,
                                        },
                                        "transcription": {"model": "gpt-realtime-whisper"},
                                    },
                                    "output": {
                                        "format": {"type": "audio/pcm", "rate": 24000},
                                        "voice": voice,
                                    },
                                },
                                "tools": realtime_tools,
                                "tool_choice": "auto",
                            },
                        }
                    )

                    handled_hermes_call_ids: set[str] = set()

                    async def client_to_openai() -> None:
                        async for message in client:
                            if message.type == web.WSMsgType.TEXT:
                                event = json.loads(message.data)
                                if event.get("type") == "session.stop":
                                    return
                                await upstream.send_str(message.data)
                            elif message.type in {web.WSMsgType.CLOSE, web.WSMsgType.ERROR}:
                                return

                    async def openai_to_client() -> None:
                        async for message in upstream:
                            if message.type != web.WSMsgType.TEXT:
                                if message.type in {web.WSMsgType.CLOSE, web.WSMsgType.ERROR}:
                                    return
                                continue
                            event = json.loads(message.data)
                            hermes_call = _completed_hermes_call(str(event.get("type") or ""), event)
                            if hermes_call is not None and hermes_call[0] not in handled_hermes_call_ids:
                                call_id, arguments = hermes_call
                                handled_hermes_call_ids.add(call_id)
                                try:
                                    answer = await self._hermes_answer(
                                        str(arguments.get("request") or ""),
                                        model=agent_model,
                                        system_prompt=system_prompt,
                                        session_id=session_id,
                                    )
                                except Exception as exc:
                                    _LOGGER.exception("Realtime ask_hermes failed")
                                    answer = f"Hermes could not complete that request: {exc}"
                                await upstream.send_json(
                                    {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": call_id,
                                            "output": answer,
                                        },
                                    }
                                )
                                await upstream.send_json({"type": "response.create"})
                            await client.send_str(message.data)

                    tasks = [
                        asyncio.create_task(client_to_openai()),
                        asyncio.create_task(openai_to_client()),
                    ]
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*done, *pending, return_exceptions=True)
        except Exception as exc:
            _LOGGER.exception("Realtime proxy failed")
            if not client.closed:
                await client.send_json({"type": "bridge.error", "error": str(exc)})
        finally:
            if not client.closed:
                await client.close()
        return client

    async def transcribe(self, request: web.Request) -> web.Response:
        self.require_auth(request)
        if not request.content_type.startswith("multipart/"):
            raise web.HTTPBadRequest(text="Expected multipart form data")
        reader = await request.multipart()
        temp_path = ""
        options: dict[str, str] = {}
        try:
            while True:
                part: Any = await reader.next()
                if part is None:
                    break
                if part.name != "file":
                    if part.name in {"provider", "model", "language"}:
                        options[part.name] = (await part.text()).strip()
                    else:
                        await part.release()
                    continue
                suffix = Path(part.filename or "audio.wav").suffix or ".wav"
                with tempfile.NamedTemporaryFile(
                    prefix="reachy-hermes-stt-", suffix=suffix, delete=False
                ) as output:
                    temp_path = output.name
                    total = 0
                    while chunk := await part.read_chunk(64 * 1024):
                        total += len(chunk)
                        if total > _MAX_AUDIO_BYTES:
                            raise web.HTTPRequestEntityTooLarge(
                                max_size=_MAX_AUDIO_BYTES, actual_size=total
                            )
                        output.write(chunk)
            if not temp_path:
                raise web.HTTPBadRequest(text="Missing audio file")

            provider = options.get("provider", "configured").lower()
            if provider == "elevenlabs":
                if self.http is None:
                    raise web.HTTPServiceUnavailable(text="Bridge HTTP client is not ready")
                eleven_key = _resolve_secret("ELEVENLABS_API_KEY", self.profile)
                if not eleven_key:
                    raise web.HTTPBadRequest(text="ElevenLabs is not configured on the Hermes host")
                model = options.get("model") or "scribe_v2"
                form = FormData()
                with open(temp_path, "rb") as audio_file:
                    form.add_field(
                        "file",
                        audio_file,
                        filename=Path(temp_path).name,
                        content_type="audio/wav",
                    )
                    form.add_field("model_id", model)
                    if language := options.get("language"):
                        form.add_field("language_code", language)
                    form.add_field("tag_audio_events", "false")
                    form.add_field("diarize", "false")
                    async with self.http.post(
                        "https://api.elevenlabs.io/v1/speech-to-text",
                        headers={"xi-api-key": eleven_key},
                        data=form,
                    ) as upstream:
                        payload = await upstream.json(content_type=None)
                        if upstream.status != 200:
                            raise web.HTTPBadRequest(
                                text=str(payload.get("detail") or "ElevenLabs transcription failed")
                            )
                return web.json_response(
                    {"text": str(payload.get("text") or "").strip(), "provider": "elevenlabs"}
                )

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

        provider = str(payload.get("provider") or "configured").lower()
        if provider == "elevenlabs":
            if self.http is None:
                raise web.HTTPServiceUnavailable(text="Bridge HTTP client is not ready")
            eleven_key = _resolve_secret("ELEVENLABS_API_KEY", self.profile)
            if not eleven_key:
                raise web.HTTPBadRequest(text="ElevenLabs is not configured on the Hermes host")
            voice = str(payload.get("voice") or "pNInz6obpgDQGcFmaJgB").strip()
            model = str(payload.get("model") or "eleven_flash_v2_5").strip()
            if not voice or not all(character.isalnum() or character in "_-" for character in voice):
                raise web.HTTPBadRequest(text="Invalid ElevenLabs voice ID")
            async with self.http.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": eleven_key, "Content-Type": "application/json"},
                json={"text": text, "model_id": model},
            ) as upstream:
                audio = await upstream.read()
                if upstream.status != 200:
                    raise web.HTTPBadRequest(text="ElevenLabs speech synthesis failed")
            return web.Response(
                body=audio,
                content_type="audio/mpeg",
                headers={"X-Reachy-TTS-Provider": "elevenlabs"},
            )

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
                headers={
                    "Content-Disposition": f'inline; filename="{actual_path.name}"',
                    "X-Reachy-TTS-Provider": str(result.get("provider") or "configured"),
                },
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
    app.router.add_get("/v1/models", bridge.models)
    app.router.add_get("/v1/voice-options", bridge.voice_options)
    app.router.add_get("/v1/realtime", bridge.realtime)
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
