from __future__ import annotations

import json

import httpx
import pytest

from reachy_mini_hermes.config import AppConfig
from reachy_mini_hermes.hermes_client import HermesBridgeClient, HermesBridgeError


def make_client(handler) -> HermesBridgeClient:
    config = AppConfig(
        bridge_url="http://bridge.test",
        api_key="secret",
        instance_id="robot-123",
        system_prompt="Speak briefly.",
    )
    transport = httpx.MockTransport(handler)
    return HermesBridgeClient(config, client=httpx.Client(transport=transport))


def test_health_chat_and_speech_contract() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/chat/completions":
            seen_headers.update(request.headers)
            body = json.loads(request.content)
            assert body["messages"][-1]["content"] == "Hello"
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "Hi from Hermes"}}]},
            )
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(200, content=b"ID3audio", headers={"content-type": "audio/mpeg"})
        raise AssertionError(request.url)

    client = make_client(handler)
    assert client.health()["status"] == "ok"
    assert client.chat("Hello") == "Hi from Hermes"
    speech = client.synthesize("Hi from Hermes")
    assert speech.data == b"ID3audio"
    assert speech.extension == ".mp3"
    assert seen_headers["authorization"] == "Bearer secret"
    assert seen_headers["x-hermes-session-key"] == "agent:main:reachy-mini:robot-123"
    assert seen_headers["x-hermes-session-id"].startswith("reachy-robot-123-")


def test_error_does_not_put_api_key_in_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = make_client(handler)
    with pytest.raises(HermesBridgeError) as error:
        client.chat("Hello")
    assert "secret" not in str(error.value)
    assert "401" in str(error.value)


def test_model_discovery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "hermes-agent", "root": "hermes-agent"},
                    {"id": "reachy-gemini", "root": "gemini-3.5-flash"},
                ]
            },
        )

    client = make_client(handler)
    assert [model["id"] for model in client.models()] == [
        "hermes-agent",
        "reachy-gemini",
    ]


def test_transcription_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer secret"
        assert b'filename="utterance.wav"' in request.content
        return httpx.Response(200, json={"text": "turn on the lights"})

    client = make_client(handler)
    assert client.transcribe(b"RIFFfake") == "turn on the lights"
