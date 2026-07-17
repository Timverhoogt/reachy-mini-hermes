from __future__ import annotations

import base64
import json

import numpy as np

from reachy_mini_hermes.realtime_client import RealtimeBridgeSession, RealtimeEvent, realtime_url


def test_realtime_url_uses_private_bridge() -> None:
    assert realtime_url("http://192.168.1.10:8643") == "ws://192.168.1.10:8643/v1/realtime"
    assert realtime_url("https://voice.example") == "wss://voice.example/v1/realtime"


def test_audio_delta_decodes_pcm16() -> None:
    expected = np.array([-1.0, 0.0, 0.5], dtype=np.float32)
    pcm = (expected * 32767).astype("<i2").tobytes()
    event = RealtimeEvent(
        "response.output_audio.delta",
        {"delta": base64.b64encode(pcm).decode("ascii")},
    )
    actual = RealtimeBridgeSession.audio_samples(event)
    assert np.allclose(actual, expected, atol=1e-4)


def test_truncate_audio_reports_played_offset() -> None:
    class Socket:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def send(self, message: str) -> None:
            self.messages.append(message)

    session = RealtimeBridgeSession.__new__(RealtimeBridgeSession)
    socket = Socket()
    session._socket = socket  # type: ignore[assignment]

    session.truncate_audio("item-123", 1450)

    assert json.loads(socket.messages[0]) == {
        "type": "conversation.item.truncate",
        "item_id": "item-123",
        "content_index": 0,
        "audio_end_ms": 1450,
    }
