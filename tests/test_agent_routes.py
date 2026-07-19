from __future__ import annotations

import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

import reachy_mini_hermes.main as main_module
from reachy_mini_hermes.config import AppConfig
from reachy_mini_hermes.main import ReachyMiniHermes
from reachy_mini_hermes.runtime import HermesVoiceRuntime


def build_client(monkeypatch):
    config = AppConfig(api_key="sk-super-secret-value")
    saved: list[AppConfig] = []
    monkeypatch.setattr(main_module, "load_config", lambda: config)
    monkeypatch.setattr(main_module, "save_config", lambda value: saved.append(value))
    app = ReachyMiniHermes(False)
    runtime = HermesVoiceRuntime(SimpleNamespace(), threading.Event(), config_loader=lambda: config)
    app._runtime = runtime
    assert app.settings_app is not None
    return app, runtime, TestClient(app.settings_app), saved


def test_agent_profile_requires_explicit_unlocked_adult_ui(monkeypatch) -> None:
    _app, _runtime, client, saved = build_client(monkeypatch)
    assert client.post("/api/agent/profile", json={"profile": "agent"}).status_code == 403

    response = client.post(
        "/api/agent/profile",
        headers={"X-Reachy-Adult-UI": "unlocked"},
        json={"profile": "agent"},
    )
    assert response.status_code == 200
    assert response.json()["agent"]["profile"] == "agent"
    assert response.json()["agent"]["enabled_capabilities"] == []
    assert saved[-1].capability_profile == "agent"


def test_agent_routes_reject_kids_lock_and_stop_invalidates_generation(monkeypatch) -> None:
    _app, runtime, client, _saved = build_client(monkeypatch)
    agent = runtime.status()["agent"]
    assert isinstance(agent, dict)
    before = int(agent["session_generation"])
    runtime._kids_locked = True
    blocked = client.post(
        "/api/agent/profile",
        headers={"X-Reachy-Adult-UI": "unlocked"},
        json={"profile": "agent"},
    )
    assert blocked.status_code == 423

    stopped = client.post("/api/agent/stop")
    assert stopped.status_code == 200
    assert stopped.json()["agent"]["session_generation"] == before + 1


def test_status_has_strict_sanitized_agent_shape(monkeypatch) -> None:
    _app, _runtime, client, _saved = build_client(monkeypatch)
    payload = client.get("/api/status").json()
    assert set(payload["runtime"]["agent"]) == {
        "profile",
        "session_generation",
        "enabled_capabilities",
        "current_task",
        "pending_approval",
        "recent_activity",
    }
    serialized = str(payload)
    assert "sk-super-secret-value" not in serialized
    for unrestricted_name in ("terminal", "read_file", "write_file", "search_files", "execute_code"):
        assert unrestricted_name not in serialized
