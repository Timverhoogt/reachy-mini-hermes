from __future__ import annotations

import base64

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
