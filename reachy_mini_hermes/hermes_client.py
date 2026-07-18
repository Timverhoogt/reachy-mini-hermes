"""HTTP client for the Hermes Reachy companion bridge."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from .config import AppConfig

_LOGGER = logging.getLogger(__name__)


class HermesBridgeError(RuntimeError):
    pass


@dataclass(slots=True)
class SpeechAudio:
    data: bytes
    content_type: str
    extension: str
    provider: str = "configured"


class HermesBridgeClient:
    """Talk to Hermes without moving provider credentials onto the robot."""

    def __init__(self, config: AppConfig, *, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client or httpx.Client(timeout=httpx.Timeout(90.0, connect=10.0), follow_redirects=False)
        self._owns_client = client is None
        self._session_id = self._new_session_id()
        self._last_turn_at = 0.0
        self.last_stt_provider = ""
        self.last_tts_provider = ""
        self._kids_history: list[dict[str, str]] = []

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _new_session_id(self) -> str:
        return f"reachy-{self.config.instance_id[:16]}-{uuid.uuid4().hex[:12]}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "X-Hermes-Session-Id": self._session_id,
            "X-Hermes-Session-Key": f"agent:main:reachy-mini:{self.config.instance_id}",
        }

    def _rotate_session_if_stale(self) -> None:
        now = time.monotonic()
        if self._last_turn_at and now - self._last_turn_at > self.config.conversation_timeout_seconds:
            self._session_id = self._new_session_id()
        self._last_turn_at = now

    def health(self) -> dict[str, object]:
        try:
            response = self._client.get(f"{self.config.bridge_url}/health", timeout=5.0)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise HermesBridgeError("Hermes bridge returned an invalid health payload")
            if payload.get("status") != "ok" or payload.get("hermes_api") is False:
                raise HermesBridgeError("Hermes bridge is running but its agent API is unavailable")
            return payload
        except Exception as exc:
            raise HermesBridgeError(f"Hermes bridge is unavailable: {exc}") from exc

    def voice_options(self) -> dict[str, object]:
        response = self._client.get(
            f"{self.config.bridge_url}/v1/voice-options",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
        )
        self._raise_for_error(response, "voice option discovery")
        payload = response.json()
        if not isinstance(payload, dict):
            raise HermesBridgeError("Hermes returned invalid voice options")
        return payload

    def transcribe(self, wav_data: bytes) -> str:
        response = self._client.post(
            f"{self.config.bridge_url}/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            data={
                "language": self.config.language,
                "provider": self.config.stt_provider,
                "model": self.config.stt_model,
            },
            files={"file": ("utterance.wav", wav_data, "audio/wav")},
        )
        self._raise_for_error(response, "transcription")
        payload = response.json()
        transcript = str(payload.get("text") or payload.get("transcript") or "").strip()
        self.last_stt_provider = str(payload.get("provider") or self.config.stt_provider)
        if not transcript:
            raise HermesBridgeError("Hermes STT returned an empty transcript")
        return transcript

    def models(self) -> list[dict[str, object]]:
        response = self._client.get(
            f"{self.config.bridge_url}/v1/models",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
        )
        self._raise_for_error(response, "model discovery")
        payload = response.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list):
            raise HermesBridgeError("Hermes returned an invalid model list")
        return [item for item in data if isinstance(item, dict) and item.get("id")]

    def chat(self, transcript: str) -> str:
        if self.config.kids_mode_enabled:
            return self._kids_chat(transcript)
        self._rotate_session_if_stale()
        response = self._client.post(
            f"{self.config.bridge_url}/v1/chat/completions",
            headers=self._headers(),
            json={
                "model": self.config.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": self.config.system_prompt},
                    {"role": "user", "content": transcript},
                ],
            },
        )
        self._raise_for_error(response, "agent response")
        payload = response.json()
        try:
            text = str(payload["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise HermesBridgeError("Hermes returned an invalid chat-completion payload") from exc
        if not text:
            raise HermesBridgeError("Hermes returned an empty response")
        return text

    def _kids_chat(self, transcript: str) -> str:
        """Use the dedicated no-agent child route with ephemeral in-process context only."""
        clean = transcript.strip()
        if not clean:
            raise HermesBridgeError("Kids Mode received an empty transcript")
        response = self._client.post(
            f"{self.config.bridge_url}/v1/kids/chat",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            json={
                "input": clean,
                "system_prompt": self.config.system_prompt,
                "session_id": self.config.kids_session_id,
                "history": self._kids_history[-8:],
            },
        )
        self._raise_for_error(response, "Kids Mode response")
        payload = response.json()
        text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
        if not text:
            raise HermesBridgeError("Kids Mode returned an empty response")
        self._kids_history.extend(
            [
                {"role": "user", "content": clean[:2_000]},
                {"role": "assistant", "content": text[:1_200]},
            ]
        )
        self._kids_history = self._kids_history[-8:]
        return text

    def iter_kids_speech(self, text: str) -> Iterator[bytes]:
        """Yield low-latency 24 kHz PCM from the bridge's fixed child TTS route."""
        clean = text.strip()
        if not self.config.kids_mode_enabled:
            raise HermesBridgeError("Kids streaming speech is only available in Kids Mode")
        if not clean:
            raise HermesBridgeError("Kids Mode received an empty speech response")
        with self._client.stream(
            "POST",
            f"{self.config.bridge_url}/v1/kids/speech/stream",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            json={"input": clean},
        ) as response:
            if not response.is_success:
                response.read()
                self._raise_for_error(response, "Kids Mode streaming speech synthesis")
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            rate = response.headers.get("x-reachy-audio-rate", "")
            if content_type != "audio/pcm" or rate != "24000":
                response.read()
                raise HermesBridgeError("Kids Mode TTS returned an unsupported audio stream")
            self.last_tts_provider = response.headers.get(
                "x-reachy-tts-provider",
                "elevenlabs-flash-stream",
            )
            received = False
            for chunk in response.iter_bytes(chunk_size=8 * 1024):
                if chunk:
                    received = True
                    yield chunk
            if not received:
                raise HermesBridgeError("Kids Mode TTS returned no streaming audio")

    def synthesize(self, text: str) -> SpeechAudio:
        response = self._client.post(
            f"{self.config.bridge_url}/v1/audio/speech",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            json={
                "provider": self.config.tts_provider,
                "model": self.config.tts_model,
                "input": text,
                "voice": self.config.tts_voice,
                "response_format": "mp3",
            },
        )
        self._raise_for_error(response, "speech synthesis")
        content_type = response.headers.get("content-type", "audio/mpeg").split(";", 1)[0]
        extension = {
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/ogg": ".ogg",
            "audio/flac": ".flac",
        }.get(content_type, ".audio")
        if not response.content:
            raise HermesBridgeError("Hermes TTS returned no audio")
        provider = response.headers.get("x-reachy-tts-provider", self.config.tts_provider)
        self.last_tts_provider = provider
        return SpeechAudio(response.content, content_type, extension, provider)

    @staticmethod
    def _raise_for_error(response: httpx.Response, operation: str) -> None:
        if response.is_success:
            return
        detail = response.text[:500]
        try:
            payload = response.json()
            detail = str(payload.get("detail") or payload.get("error") or detail)
        except Exception:
            pass
        _LOGGER.warning("Hermes %s failed with HTTP %s", operation, response.status_code)
        raise HermesBridgeError(f"Hermes {operation} failed (HTTP {response.status_code}): {detail}")
