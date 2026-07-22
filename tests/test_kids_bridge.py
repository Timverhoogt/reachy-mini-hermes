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
        assert body["session_id"] == "kids-" + "a" * 32
        assert body["profile"] == {"age_band": "7-9", "activity": "riddles", "language": "en"}
        assert "history" not in body
        assert "system_prompt" not in body
        return httpx.Response(
            200,
            json={
                "text": "What has keys but no locks?",
                "speech_approval": "approved-once",
                "fallback_speech_approval": "approved-fallback-once",
            },
        )

    config = AppConfig(
        bridge_url="http://bridge.test",
        api_key="secret",
        kids_mode_enabled=True,
        kids_session_id="kids-" + "a" * 32,
        kids_age_band="7-9",
        kids_activity="riddles",
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
        assert json.loads(request.content) == {
            "input": "A short answer",
            "session_id": "kids-" + "b" * 32,
            "speech_approval": "approved-once",
        }
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
        kids_session_id="kids-" + "b" * 32,
        kids_age_band="7-9",
        kids_activity="buddy",
    )
    client = HermesBridgeClient(config, client=httpx.Client(transport=httpx.MockTransport(handler)))
    client._kids_speech_approval = "approved-once"

    assert b"".join(client.iter_kids_speech("A short answer")) == b"\x01\x00\x02\x00"
    assert client.last_tts_provider == "elevenlabs-flash-stream"
    assert "x-hermes-session-key" not in seen[0].headers
    assert "x-hermes-session-id" not in seen[0].headers


def test_kids_client_fallback_speech_carries_separate_exact_approval() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/kids/speech/fallback"
        body = json.loads(request.content)
        assert body["input"] == "Approved answer"
        assert body["session_id"] == "kids-" + "d" * 32
        assert body["speech_approval"] == "approved-fallback-once"
        return httpx.Response(
            200,
            headers={"content-type": "audio/mpeg", "x-reachy-tts-provider": "configured"},
            content=b"fallback-audio",
        )

    config = AppConfig(
        bridge_url="http://bridge.test",
        api_key="secret",
        kids_mode_enabled=True,
        kids_session_id="kids-" + "d" * 32,
        kids_age_band="7-9",
        kids_activity="buddy",
    )
    client = HermesBridgeClient(config, client=httpx.Client(transport=httpx.MockTransport(handler)))
    client._kids_fallback_speech_approval = "approved-fallback-once"

    speech = client.synthesize("Approved answer")
    assert speech.data == b"fallback-audio"
    assert client._kids_fallback_speech_approval == ""


def test_kids_policy_and_history_are_bridge_authoritative() -> None:
    source = (ROOT / "companion" / "hermes_reachy_bridge.py").read_text(encoding="utf-8")
    method = source.split("    async def kids_chat", 1)[1].split("    async def kids_ispy_select", 1)[0]

    assert "_build_bridge_kids_prompt" in method
    assert 'payload.get("system_prompt")' not in method
    assert 'payload.get("history")' not in method
    assert 'child_session["history"]' in method
    assert "profile cannot change" in method


def test_bridge_kids_route_has_moderation_on_both_sides_and_no_hermes_forwarding() -> None:
    source = (ROOT / "companion" / "hermes_reachy_bridge.py").read_text(encoding="utf-8")
    method = source.split("    async def kids_chat", 1)[1].split("    async def kids_ispy_select", 1)[0]

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
    assert "_consume_kids_speech_approval" in method
    assert "eleven_multilingual_v2" not in method
