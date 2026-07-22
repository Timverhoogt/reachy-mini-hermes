from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import companion.reachy_agent_broker as broker_module
from companion.reachy_agent_broker import (
    BrokerConfig,
    BrokerValidationError,
    ReachyAgentBroker,
    _read_scoped_file,
    _require_public_url,
    redact_payload,
)


class UnusedHttp:
    def get(self, *_args, **_kwargs):  # pragma: no cover - guards accidental network use
        raise AssertionError("unexpected network request")


def context(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "capability_profile": "agent",
        "adult_ui_unlocked": True,
        "kids_mode_active": False,
        "power_mode": "awake",
        "privacy_enabled": True,
        "emergency_stop_active": False,
        "robot_available": True,
        "session_generation": 4,
        "requested_session_generation": 4,
        "explicit_private_intent": True,
        "reachy_status": {"state": "listening", "password": "never-return", "motors_enabled": True},
    }
    payload.update(changes)
    return payload


def request(capability: str, arguments: dict[str, object], **context_changes: object) -> dict[str, object]:
    return {
        "request_id": "agent-request-1234",
        "capability_id": capability,
        "arguments": arguments,
        "context": context(**context_changes),
    }


async def broker_execute(
    broker: ReachyAgentBroker,
    payload: dict[str, object],
    http: object,
    *,
    device_id: str = "test-device",
) -> dict[str, object]:
    await broker.establish_session(device_id, payload["context"])  # type: ignore[arg-type]
    return await broker.execute(payload, http, device_id=device_id)  # type: ignore[arg-type]


def test_manifest_is_fixed_typed_owner_surface() -> None:
    manifest = ReachyAgentBroker(BrokerConfig()).manifest()
    assert {item["id"] for item in manifest} == {
        "get_agent_capabilities",
        "get_reachy_status",
        "get_home_status",
        "search_current_information",
        "read_public_web_page",
        "recall_personal_context",
        "search_conversation_history",
        "read_scoped_note",
        "control_home_entity",
        "set_timer",
        "cancel_timer",
        "create_reminder",
        "cancel_reminder",
        "play_media",
        "pause_media",
        "set_media_volume",
        "undo_last_reversible_action",
        "list_calendar_events",
        "draft_calendar_event",
        "create_calendar_event",
        "draft_message",
        "send_approved_message",
        "draft_note",
        "append_scoped_note",
    }
    assert all(item["cancellable"] is True for item in manifest)
    assert not any(item["risk_tier"] == "T4_PRIVILEGED" for item in manifest)
    assert all(
        item["requires_approval"]
        for item in manifest
        if item["risk_tier"] == "T3_EXTERNAL_SIDE_EFFECT"
    )
    assert all(item["arguments_schema"]["additionalProperties"] is False for item in manifest)


def test_execution_returns_evidence_freshness_and_sanitized_status() -> None:
    broker = ReachyAgentBroker(BrokerConfig())
    payload = request("get_reachy_status", {})
    request_context = payload["context"]
    assert isinstance(request_context, dict)
    reachy_status = request_context["reachy_status"]
    assert isinstance(reachy_status, dict)
    reachy_status["observed_at"] = 1_700_000_000.0
    result = asyncio.run(broker_execute(broker, payload, UnusedHttp()))
    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["side_effect"] is False
    assert result["data"] == {"state": "listening", "motors_enabled": True}
    assert result["evidence"][0]["source"] == "reachy_runtime"
    assert result["freshness"]["observed_at"] == 1_700_000_000.0
    assert set(result["freshness"]) == {"observed_at", "completed_at", "age_seconds"}
    activity = asyncio.run(broker.recent_activity())
    assert [item["event"] for item in activity] == ["started", "completed"]
    assert "never-return" not in json.dumps(result)


@pytest.mark.parametrize(
    "change",
    [
        {"capability_profile": "conversation"},
        {"adult_ui_unlocked": False},
        {"kids_mode_active": True},
        {"power_mode": "meeting"},
        {"power_mode": "sleep"},
        {"privacy_enabled": False},
        {"emergency_stop_active": True},
        {"requested_session_generation": 3},
    ],
)
def test_authorization_fails_closed(change: dict[str, object]) -> None:
    broker = ReachyAgentBroker(BrokerConfig())
    with pytest.raises(BrokerValidationError):
        asyncio.run(broker_execute(broker, request("get_reachy_status", {}, **change), UnusedHttp()))


def test_private_reads_require_current_turn_intent() -> None:
    broker = ReachyAgentBroker(BrokerConfig(home_entities={"sensor.safe": frozenset()}))
    with pytest.raises(BrokerValidationError, match="explicit_private_intent"):
        asyncio.run(
            broker_execute(
                broker,
                request("get_home_status", {}, explicit_private_intent=False),
                UnusedHttp(),  # type: ignore[arg-type]
            )
        )


def test_scoped_note_rejects_traversal_and_symlinks_and_redacts(tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "safe.md").write_text("Useful preference\napi_key=super-secret-value\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("private outside", encoding="utf-8")
    (notes / "escape.md").symlink_to(outside)
    broker = ReachyAgentBroker(BrokerConfig(note_roots={"notes": notes}))

    result = asyncio.run(
        broker_execute(broker, request("read_scoped_note", {"root": "notes", "path": "safe.md"}), UnusedHttp())
    )
    assert "Useful preference" in result["data"]["text"]
    assert "super-secret-value" not in json.dumps(result)
    assert "[redacted]" in result["data"]["text"]

    (notes / "json-secret.json").write_text('{"token": "multi word secret"}', encoding="utf-8")
    json_result = asyncio.run(
        broker_execute(
            broker,
            request("read_scoped_note", {"root": "notes", "path": "json-secret.json"}),
            UnusedHttp(),  # type: ignore[arg-type]
        )
    )
    assert "multi word secret" not in json.dumps(json_result)

    for unsafe in ("../outside.md", "escape.md", "/etc/passwd"):
        with pytest.raises(BrokerValidationError):
            asyncio.run(
                broker_execute(
                    broker,
                    request("read_scoped_note", {"root": "notes", "path": unsafe}),
                    UnusedHttp(),  # type: ignore[arg-type]
                )
            )


def test_home_status_returns_only_allowlisted_attributes_and_redacts() -> None:
    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def json(self, **_kwargs):
            return {
                "entity_id": "sensor.living_room",
                "state": "21.5",
                "attributes": {
                    "friendly_name": "Living room",
                    "unit_of_measurement": "°C",
                    "access_token": "must-not-return",
                },
            }

    class Http:
        def get(self, url: str, **kwargs):
            assert url.endswith("/api/states/sensor.living_room")
            assert kwargs["allow_redirects"] is False
            assert kwargs["headers"]["Authorization"] == "Bearer host-secret"
            return Response()

    broker = ReachyAgentBroker(
        BrokerConfig(
            home_entities={"sensor.living_room": frozenset({"friendly_name"})},
            hass_url="http://home-assistant.test",
            hass_token="host-secret",
        )
    )
    result = asyncio.run(broker_execute(broker, request("get_home_status", {}), Http()))
    serialized = json.dumps(result)
    assert "Living room" in serialized
    assert "unit_of_measurement" not in serialized
    assert "access_token" not in serialized
    assert "host-secret" not in serialized


def test_local_and_sensitive_public_urls_are_rejected() -> None:
    with pytest.raises(BrokerValidationError, match="non-public"):
        asyncio.run(_require_public_url("http://127.0.0.1/private"))
    with pytest.raises(BrokerValidationError, match="sensitive query"):
        asyncio.run(_require_public_url("https://example.com/page?token=secret"))


def test_inflight_execution_is_cancellable_and_has_no_success_event() -> None:
    broker = ReachyAgentBroker(BrokerConfig(timeout_seconds=10))

    async def slow_dispatch(*_args, **_kwargs):
        await asyncio.sleep(10)
        return {}, [], 0.0, False

    broker._dispatch = slow_dispatch  # type: ignore[method-assign]

    async def scenario() -> list[dict[str, object]]:
        payload = request("get_reachy_status", {})
        await broker.establish_session("test-device", payload["context"])  # type: ignore[arg-type]
        task = asyncio.create_task(broker.execute(payload, UnusedHttp()))  # type: ignore[arg-type]
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await broker.recent_activity()

    activity = asyncio.run(scenario())
    assert [item["event"] for item in activity] == ["started", "cancelled"]


@pytest.mark.parametrize(
    "change",
    [
        {"capability_profile": "future-profile"},
        {"capability_profile": 1},
        {"power_mode": "unknown"},
        {"power_mode": True},
        {"session_generation": "4"},
    ],
)
def test_context_schema_rejects_unknown_enums_and_coercions(change: dict[str, object]) -> None:
    with pytest.raises(BrokerValidationError):
        asyncio.run(
            broker_execute(
                ReachyAgentBroker(BrokerConfig()),
                request("get_reachy_status", {}, **change), UnusedHttp()  # type: ignore[arg-type]
            )
        )


def test_newer_device_generation_cancels_old_work_and_invalidates_activity() -> None:
    broker = ReachyAgentBroker(BrokerConfig(timeout_seconds=10))

    async def slow_dispatch(*_args, **_kwargs):
        await asyncio.sleep(10)
        return {}, [], 0.0, False

    broker._dispatch = slow_dispatch  # type: ignore[method-assign]

    async def scenario() -> None:
        old_payload = request("get_reachy_status", {})
        await broker.establish_session("reachy-a", old_payload["context"])  # type: ignore[arg-type]
        old = asyncio.create_task(
            broker.execute(
                old_payload,
                UnusedHttp(),  # type: ignore[arg-type]
                device_id="reachy-a",
            )
        )
        await asyncio.sleep(0)
        newer_context = context(session_generation=5, requested_session_generation=5)
        parsed = await broker.establish_session("reachy-a", newer_context)
        broker.authorize_context(parsed)
        with pytest.raises(asyncio.CancelledError):
            await old
        assert await broker.recent_activity("reachy-a", 5) == []
        with pytest.raises(BrokerValidationError, match="stale_session"):
            await broker.recent_activity("reachy-a", 4)

    asyncio.run(scenario())


def test_request_payload_cannot_create_or_promote_authoritative_lease() -> None:
    broker = ReachyAgentBroker(BrokerConfig())

    async def scenario() -> None:
        with pytest.raises(BrokerValidationError, match="stale_session"):
            await broker.execute(request("get_reachy_status", {}), UnusedHttp())  # type: ignore[arg-type]
        await broker.establish_session("test-device", context())
        promoted = request(
            "get_reachy_status", {}, session_generation=5, requested_session_generation=5
        )
        with pytest.raises(BrokerValidationError, match="stale_session"):
            await broker.execute(promoted, UnusedHttp())  # type: ignore[arg-type]

    asyncio.run(scenario())


def test_success_handoff_atomically_unregisters_the_completed_request() -> None:
    broker = ReachyAgentBroker(BrokerConfig())

    async def scenario() -> None:
        await broker.establish_session("test-device", context())
        result = await broker.execute(request("get_reachy_status", {}), UnusedHttp())  # type: ignore[arg-type]
        assert result["ok"] is True
        async with broker._leases_lock:
            lease = broker._leases["test-device"]
            assert lease.generation == 4
            assert lease.tasks == {}
        activity = await broker.recent_activity("test-device", 4)
        assert [item["event"] for item in activity] == ["started", "completed"]

    asyncio.run(scenario())


def test_activity_is_scoped_by_authenticated_device() -> None:
    broker = ReachyAgentBroker(BrokerConfig())

    async def scenario() -> None:
        first = request("get_reachy_status", {})
        first["request_id"] = "agent-device-a"
        second = request("get_reachy_status", {})
        second["request_id"] = "agent-device-b"
        await broker_execute(broker, first, UnusedHttp(), device_id="reachy-a")
        await broker_execute(broker, second, UnusedHttp(), device_id="reachy-b")
        activity_a = await broker.recent_activity("reachy-a", 4)
        activity_b = await broker.recent_activity("reachy-b", 4)
        assert {item["request_id"] for item in activity_a} == {"agent-device-a"}
        assert {item["request_id"] for item in activity_b} == {"agent-device-b"}

    asyncio.run(scenario())


def test_scoped_read_uses_validated_descriptor_during_path_swap(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "notes"
    root.mkdir()
    safe = root / "safe.md"
    safe.write_text("approved content", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside secret", encoding="utf-8")
    original_fstat = broker_module.os.fstat
    swapped = False

    def swap_after_open(descriptor: int):
        nonlocal swapped
        metadata = original_fstat(descriptor)
        if not swapped:
            swapped = True
            safe.rename(root / "original.md")
            safe.symlink_to(outside)
        return metadata

    monkeypatch.setattr(broker_module.os, "fstat", swap_after_open)
    assert _read_scoped_file(root, Path("safe.md")) == b"approved content"


def test_central_dlp_redacts_provider_tokens_jwts_and_private_keys() -> None:
    payload = {
        "text": (
            "sk-proj-abcdefghijklmnopqrstuvwxyz "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop "
            "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----"
        )
    }
    serialized = json.dumps(redact_payload(payload))
    assert "private-material" not in serialized
    assert "eyJhbGci" not in serialized
    assert "sk-proj" not in serialized
    assert serialized.count("[redacted]") == 3


def test_central_dlp_drops_underscored_secret_fields() -> None:
    payload = {
        "access_token": "access-value",
        "refresh_token": "refresh-value",
        "client_secret": "client-value",
        "safe": "client_secret=inline-value",
    }
    serialized = json.dumps(redact_payload(payload))
    for secret in ("access-value", "refresh-value", "client-value", "inline-value"):
        assert secret not in serialized
    assert json.loads(serialized) == {"safe": "[redacted]"}
