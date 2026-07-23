from __future__ import annotations

import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

import reachy_mini_hermes.main as main_module
from reachy_mini_hermes.agent_audit import AgentAuditLog
from reachy_mini_hermes.config import AppConfig
from reachy_mini_hermes.kids_mode import KidsProfile
from reachy_mini_hermes.main import ReachyMiniHermes
from reachy_mini_hermes.runtime import HermesVoiceRuntime


def build_client(monkeypatch):
    config = AppConfig(api_key="sk-super-secret-value")
    saved: list[AppConfig] = []
    monkeypatch.setattr(main_module, "load_config", lambda: config)
    monkeypatch.setattr(main_module, "save_config", lambda value: saved.append(value))
    app = ReachyMiniHermes(False)
    runtime = HermesVoiceRuntime(SimpleNamespace(), threading.Event(), config_loader=lambda: config)
    runtime._establish_remote_agent_session = lambda _context: None  # type: ignore[method-assign]
    runtime._publish_remote_agent_session = lambda: None  # type: ignore[method-assign]
    runtime._cancel_remote_agent_request = lambda _request_id: None  # type: ignore[method-assign]
    app._runtime = runtime
    assert app.settings_app is not None
    return app, runtime, TestClient(app.settings_app), saved


def test_early_power_route_rejects_before_motion(monkeypatch) -> None:
    _app, runtime, client, _saved = build_client(monkeypatch)
    calls: list[tuple[bool, bool]] = []
    runtime._runtime_started = True
    runtime._set_motor_mode = (  # type: ignore[method-assign]
        lambda enabled, wake=False: calls.append((enabled, wake))
    )

    response = client.post("/api/power", json={"mode": "awake"})

    assert response.status_code == 409
    assert response.json()["detail"] == "Voice runtime is still starting; no power transition was attempted"
    assert calls == []


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
    enabled = set(response.json()["agent"]["enabled_capabilities"])
    assert {
        "get_agent_capabilities",
        "get_reachy_status",
        "get_home_status",
        "search_current_information",
        "read_public_web_page",
        "recall_personal_context",
        "search_conversation_history",
        "read_scoped_note",
    } <= enabled
    assert {"control_home_entity", "set_timer", "draft_message", "append_scoped_note"} <= enabled
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


def test_fresh_runtime_uses_newer_agent_generation_after_restart(monkeypatch) -> None:
    epochs = iter((1_000_000_000, 2_000_000_000))
    monkeypatch.setattr("reachy_mini_hermes.runtime.time.time_ns", lambda: next(epochs))

    first = HermesVoiceRuntime(SimpleNamespace(), threading.Event())
    second = HermesVoiceRuntime(SimpleNamespace(), threading.Event())

    first_agent = first.status()["agent"]
    second_agent = second.status()["agent"]
    assert isinstance(first_agent, dict)
    assert isinstance(second_agent, dict)
    first_generation = int(first_agent["session_generation"])
    second_generation = int(second_agent["session_generation"])
    assert first_generation == 1_000_000_000
    assert second_generation == 2_000_000_000
    assert second_generation > first_generation


def test_kids_start_wins_over_racing_agent_enable() -> None:
    runtime = HermesVoiceRuntime(SimpleNamespace(media=SimpleNamespace()), threading.Event())
    runtime._audio_ready = True
    agent_reached_privacy_check = threading.Event()
    allow_agent_to_continue = threading.Event()
    agent_errors: list[Exception] = []

    def controlled_power_mode() -> str:
        if threading.current_thread().name == "agent-enable":
            agent_reached_privacy_check.set()
            assert allow_agent_to_continue.wait(timeout=2.0)
        return "awake"

    runtime._effective_power_mode = controlled_power_mode  # type: ignore[method-assign]

    def enable_agent() -> None:
        try:
            runtime.set_capability_profile("agent", adult_ui_unlocked=True)
        except Exception as exc:
            agent_errors.append(exc)

    agent_thread = threading.Thread(target=enable_agent, name="agent-enable")
    agent_thread.start()
    assert agent_reached_privacy_check.wait(timeout=2.0)

    runtime.start_kids_mode(KidsProfile(duration_minutes=15), greet=False)
    allow_agent_to_continue.set()
    agent_thread.join(timeout=2.0)

    assert not agent_thread.is_alive()
    assert len(agent_errors) == 1
    assert isinstance(agent_errors[0], RuntimeError)
    with runtime._kids_lock, runtime._agent_lock:
        assert runtime._capability_profile == "conversation"
        assert runtime._kids_active is True
    runtime.stop_kids_mode(fold=False)


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


def test_status_never_exposes_agent_transcript_or_secret(monkeypatch) -> None:
    _app, runtime, client, _saved = build_client(monkeypatch)
    runtime.set_capability_profile("agent", adult_ui_unlocked=True)
    secret_request = "Read my note using " + "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz"
    runtime._begin_agent_request(secret_request)
    with runtime._status_lock:
        runtime._status.transcript = secret_request
        runtime._status.response_preview = "private model answer with client_secret=hidden"
    payload = client.get("/api/status").json()
    serialized = str(payload)
    assert secret_request not in serialized
    assert "sk-proj" not in serialized
    assert "transcript" not in payload["runtime"]
    assert "response_preview" not in payload["runtime"]
    assert payload["runtime"]["agent"]["current_task"] == "Processing a bounded owner request"


def test_agent_activity_is_server_gated_by_adult_and_kids_state(monkeypatch) -> None:
    _app, runtime, client, _saved = build_client(monkeypatch)
    runtime.set_capability_profile("agent", adult_ui_unlocked=True)
    assert client.get("/api/agent/activity").status_code == 403
    runtime._kids_locked = True
    response = client.get(
        "/api/agent/activity", headers={"X-Reachy-Adult-UI": "unlocked"}
    )
    assert response.status_code == 423


def test_pending_approval_routes_require_adult_and_preserve_exact_body(monkeypatch) -> None:
    _app, runtime, client, _saved = build_client(monkeypatch)
    runtime.set_capability_profile("agent", adult_ui_unlocked=True)
    pending = {
        "draft_id": "draft-0123456789abcdef01234567",
        "capability_id": "send_approved_message",
        "arguments": {"channel": "mobile", "recipient": "tim", "text": "Exact body"},
        "expires_in_seconds": 300,
    }
    approved: list[str] = []

    class BridgeClient:
        def __init__(self, _config: AppConfig) -> None:
            pass

        def pending_agent_approval(self, _context):
            return pending

        def approve_pending_agent_action(self, draft_id: str, _context):
            approved.append(draft_id)
            return {"ok": True, "data": {"verified": True}, "side_effect": True}

        def close(self) -> None:
            pass

    monkeypatch.setattr(main_module, "HermesBridgeClient", BridgeClient)
    assert client.get("/api/agent/pending-approval").status_code == 403
    fetched = client.get(
        "/api/agent/pending-approval",
        headers={"X-Reachy-Adult-UI": "unlocked"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["pending_approval"]["arguments"]["text"] == "Exact body"
    completed = client.post(
        "/api/agent/approve-pending",
        headers={"X-Reachy-Adult-UI": "unlocked"},
        json={"draft_id": pending["draft_id"]},
    )
    assert completed.status_code == 200
    assert completed.json()["verified"] is True
    assert approved == [pending["draft_id"]]


def test_timer_delivery_is_authenticated_and_blocked_by_kids(monkeypatch) -> None:
    _app, runtime, client, _saved = build_client(monkeypatch)
    monkeypatch.setattr(main_module, "load_config", lambda: AppConfig(api_key="bridge-secret"))
    queued: list[tuple[str, dict[str, object]]] = []
    runtime.queue_announcement = (  # type: ignore[method-assign]
        lambda text, **kwargs: queued.append((text, kwargs)) or {"queued": True}
    )
    body = {"item_id": "timer-0123456789abcdef", "text": "Timer finished."}
    assert client.post("/api/agent/reminder-delivery", json=body).status_code == 401
    delivered = client.post(
        "/api/agent/reminder-delivery",
        headers={"Authorization": "Bearer bridge-secret"},
        json=body,
    )
    assert delivered.status_code == 200
    assert queued == [
        (
            "Timer finished.",
            {"behavior": "voice_only", "repeat": 1, "pause_seconds": 0.0},
        )
    ]
    runtime._kids_locked = True
    blocked = client.post(
        "/api/agent/reminder-delivery",
        headers={"Authorization": "Bearer bridge-secret"},
        json=body,
    )
    assert blocked.status_code == 423


def test_agent_activity_poll_does_not_claim_or_finish_active_voice_request(monkeypatch) -> None:
    _app, runtime, client, _saved = build_client(monkeypatch)
    runtime.set_capability_profile("agent", adult_ui_unlocked=True)
    active_request_id, active_context = runtime._begin_agent_request("private owner request")

    class BridgeClient:
        def __init__(self, _config: AppConfig) -> None:
            pass

        def establish_agent_session(self, context) -> None:
            assert context.session_generation == active_context.session_generation

        def agent_activity(self, context, *, request_id: str):
            assert context.session_generation == active_context.session_generation
            assert request_id.startswith("agent-activity-")
            return [
                {
                    "event": "started",
                    "capability_id": "get_home_status",
                    "result_class": "running",
                }
            ]

        def close(self) -> None:
            pass

    monkeypatch.setattr(main_module, "HermesBridgeClient", BridgeClient)
    response = client.get(
        "/api/agent/activity", headers={"X-Reachy-Adult-UI": "unlocked"}
    )

    assert response.status_code == 200
    assert response.json()["activity"][0]["result_class"] == "running"
    with runtime._agent_lock:
        assert runtime._agent_active_request_id == active_request_id
        assert runtime._agent_current_task == "Processing a bounded owner request"
    assert runtime._finish_agent_request(
        active_request_id, active_context.session_generation, succeeded=True
    )


def test_agent_activity_cannot_return_after_kids_lock_races_request(monkeypatch) -> None:
    _app, runtime, client, _saved = build_client(monkeypatch)
    runtime.set_capability_profile("agent", adult_ui_unlocked=True)
    started = threading.Event()
    release = threading.Event()

    class BridgeClient:
        def __init__(self, _config: AppConfig) -> None:
            pass

        def establish_agent_session(self, _context) -> None:
            pass

        def agent_activity(self, _context, *, request_id: str):
            started.set()
            assert release.wait(timeout=2.0)
            return [{"event": "adult-secret"}]

        def close(self) -> None:
            pass

    monkeypatch.setattr(main_module, "HermesBridgeClient", BridgeClient)
    responses = []

    def fetch() -> None:
        responses.append(
            client.get("/api/agent/activity", headers={"X-Reachy-Adult-UI": "unlocked"})
        )

    worker = threading.Thread(target=fetch)
    worker.start()
    assert started.wait(timeout=2.0)
    with runtime._kids_lock:
        runtime._kids_active = True
        runtime._kids_locked = True
        runtime.cancel_agent_work("kids_mode")
    release.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert responses[0].status_code == 423
    assert "adult-secret" not in responses[0].text


def test_agent_audit_result_classes_match_request_outcomes(tmp_path) -> None:
    audit = AgentAuditLog(tmp_path / "agent-audit.jsonl")
    runtime = HermesVoiceRuntime(SimpleNamespace(), threading.Event(), agent_audit=audit)
    runtime._establish_remote_agent_session = lambda _context: None  # type: ignore[method-assign]
    runtime._publish_remote_agent_session = lambda: None  # type: ignore[method-assign]
    runtime.set_capability_profile("agent", adult_ui_unlocked=True)
    request_id, context = runtime._begin_agent_request("secret request body")
    assert runtime._finish_agent_request(request_id, context.session_generation, succeeded=True)
    request_id, context = runtime._begin_agent_request("another secret request")
    assert runtime._finish_agent_request(request_id, context.session_generation, succeeded=False)
    result_classes = {
        (item.get("reason"), item.get("result_class")) for item in audit.recent(limit=10)
    }
    assert ("request_started", "running") in result_classes
    assert ("request_completed", "success") in result_classes
    assert ("request_failed", "failed") in result_classes
