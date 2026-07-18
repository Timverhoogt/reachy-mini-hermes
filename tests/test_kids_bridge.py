from __future__ import annotations

import json
from pathlib import Path

import httpx

from reachy_mini_hermes.config import AppConfig
from reachy_mini_hermes.hermes_client import HermesBridgeClient

ROOT = Path(__file__).resolve().parents[1]


def test_kids_client_uses_dedicated_route_without_parent_session_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/v1/kids/chat"
        body = json.loads(request.content)
        assert body["input"] == "Tell me a riddle"
        assert body["session_id"] == "kids-random-session"
        assert body["history"] == []
        return httpx.Response(200, json={"text": "What has keys but no locks?"})

    config = AppConfig(
        bridge_url="http://bridge.test",
        api_key="secret",
        kids_mode_enabled=True,
        kids_session_id="kids-random-session",
        system_prompt="Child-safe riddle mode.",
    )
    client = HermesBridgeClient(config, client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.chat("Tell me a riddle") == "What has keys but no locks?"
    assert "x-hermes-session-key" not in seen[0].headers
    assert "x-hermes-session-id" not in seen[0].headers


def test_kids_client_streams_fixed_pcm_without_parent_session_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/v1/kids/speech/stream"
        assert json.loads(request.content) == {"input": "A short answer"}
        return httpx.Response(
            200,
            headers={
                "content-type": "audio/pcm",
                "x-reachy-audio-rate": "24000",
                "x-reachy-tts-provider": "elevenlabs-flash-stream",
            },
            content=b"\x01\x00\x02\x00",
        )

    config = AppConfig(
        bridge_url="http://bridge.test",
        api_key="secret",
        kids_mode_enabled=True,
        kids_session_id="kids-stream",
    )
    client = HermesBridgeClient(config, client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert b"".join(client.iter_kids_speech("A short answer")) == b"\x01\x00\x02\x00"
    assert client.last_tts_provider == "elevenlabs-flash-stream"
    assert "x-hermes-session-key" not in seen[0].headers
    assert "x-hermes-session-id" not in seen[0].headers


def test_kids_client_keeps_only_bounded_ephemeral_context() -> None:
    histories: list[list[dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        histories.append(body["history"])
        return httpx.Response(200, json={"text": f"Answer {len(histories)}"})

    config = AppConfig(
        bridge_url="http://bridge.test",
        api_key="secret",
        kids_mode_enabled=True,
        kids_session_id="kids-context",
        system_prompt="Child-safe quiz mode.",
    )
    client = HermesBridgeClient(config, client=httpx.Client(transport=httpx.MockTransport(handler)))
    for index in range(6):
        client.chat(f"Question {index}")

    assert histories[0] == []
    assert len(histories[-1]) == 8
    assert histories[-1][0]["role"] == "user"
    assert histories[-1][-1]["role"] == "assistant"


def test_bridge_kids_route_has_moderation_on_both_sides_and_no_hermes_forwarding() -> None:
    source = (ROOT / "companion" / "hermes_reachy_bridge.py").read_text(encoding="utf-8")
    method = source.split("    async def kids_chat", 1)[1].split("    async def _hermes_answer", 1)[0]

    assert method.count("await self._moderation_flagged") == 2
    assert "https://api.openai.com/v1/chat/completions" in method
    assert "max_completion_tokens" in method
    assert '"store": False' in method
    assert "self.hermes_url" not in method
    assert "ask_hermes" not in method
    assert "X-Hermes-Session-Key" not in method


def test_bridge_kids_speech_route_is_fixed_to_flash_pcm_streaming() -> None:
    source = (ROOT / "companion" / "hermes_reachy_bridge.py").read_text(encoding="utf-8")
    method = source.split("    async def kids_speech_stream", 1)[1].split(
        "    async def speech",
        1,
    )[0]

    assert "/stream" in method
    assert '"output_format": "pcm_24000"' in method
    assert '"model_id": "eleven_flash_v2_5"' in method
    assert "ELEVENLABS_KIDS_VOICE_ID" in method
    assert "eleven_multilingual_v2" not in method
