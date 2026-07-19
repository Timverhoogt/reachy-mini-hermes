from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import reachy_mini_hermes.main as main_module
from reachy_mini_hermes.config import AppConfig
from reachy_mini_hermes.main import ReachyMiniHermes
from reachy_mini_hermes.runtime import HermesVoiceRuntime


def test_locked_status_route_uses_child_allowlist_and_redacts_runtime_text(monkeypatch) -> None:
    app = ReachyMiniHermes(False)
    runtime = HermesVoiceRuntime(SimpleNamespace(), threading.Event())
    runtime._kids_locked = True
    with runtime._status_lock:
        runtime._status.detail = "private detail"
        runtime._status.transcript = "private child transcript"
        runtime._status.response_preview = "private model answer"
        runtime._status.announcement_current_preview = "Hello private nickname"
        runtime._status.announcement_last_text = "Hello private nickname"
        runtime._status.announcement_last_error = "private bridge URL"
        runtime._status.camera_last_error = "private camera error"
        runtime._status.robot_action_last_error = "private robot error"
        runtime._status.stt_provider = "private-stt"
        runtime._status.tts_provider = "private-tts"
        runtime._status.last_error = "private provider error"
    app._runtime = runtime
    config = AppConfig(
        bridge_url="https://private-bridge.invalid",
        api_key="private-key",
        system_prompt="private management prompt",
        kids_parent_pin_hash="scrypt$fixture",
    )
    monkeypatch.setattr(main_module, "load_config", lambda: config)

    response = TestClient(app.settings_app).get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["config"] == {
        "configured": True,
        "api_key_configured": True,
        "kids_parent_pin_configured": True,
    }
    assert set(payload["runtime"]) == {
        "state",
        "power_mode",
        "motors_enabled",
        "head_safely_folded",
        "kids_mode",
    }
    serialized = str(payload)
    for private_value in (
        "private child transcript",
        "private model answer",
        "Hello private nickname",
        "private bridge URL",
        "private provider error",
        "private detail",
        "private camera error",
        "private robot error",
        "private-stt",
        "private-tts",
        "private management prompt",
        "https://private-bridge.invalid",
        "private-key",
        "scrypt$fixture",
    ):
        assert private_value not in serialized


def test_parent_pin_failures_trigger_server_side_lockout() -> None:
    app = ReachyMiniHermes(False)
    for _ in range(5):
        app._record_kids_pin_result(valid=False)

    with pytest.raises(HTTPException) as raised:
        app._require_kids_pin_attempt_allowed()

    assert raised.value.status_code == 429
    assert raised.value.headers is not None
    assert int(raised.value.headers["Retry-After"]) >= 299
    app._record_kids_pin_result(valid=True)
    app._require_kids_pin_attempt_allowed()
