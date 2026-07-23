from __future__ import annotations

import asyncio
from typing import Any

import pytest

from companion.reachy_agent_runs import (
    AgentRunManager,
    AgentRunValidationError,
    RunBudgets,
)

CONTEXT = {
    "capability_profile": "agent",
    "adult_ui_unlocked": True,
    "kids_mode_active": False,
    "power_mode": "standby",
    "privacy_enabled": True,
    "emergency_stop_active": False,
    "robot_available": True,
    "session_generation": 7,
    "requested_session_generation": 7,
    "explicit_private_intent": True,
    "reachy_status": {},
}
MANIFEST = [
    {
        "id": "read_one",
        "description": "Read one source.",
        "risk_tier": "T0_PUBLIC_READ",
        "read_only": True,
        "requires_approval": False,
    },
    {
        "id": "read_two",
        "description": "Read another source.",
        "risk_tier": "T1_PRIVATE_READ",
        "read_only": True,
        "requires_approval": False,
    },
    {
        "id": "change_one",
        "description": "Perform one approved change.",
        "risk_tier": "T3_EXTERNAL_SIDE_EFFECT",
        "read_only": False,
        "requires_approval": True,
    },
]


def validate(capability_id: str, arguments: dict[str, object]) -> None:
    if capability_id not in {item["id"] for item in MANIFEST}:
        raise ValueError("unknown")
    if set(arguments) - {"value", "delay"}:
        raise ValueError("extra argument")


class Broker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.started = asyncio.Event()

    async def assert_current(self, _device_id: str, generation: int) -> None:
        if generation != 7:
            raise RuntimeError("stale")

    async def issue_approval(
        self,
        _device_id: str,
        _context: object,
        capability_id: str,
        _arguments: dict[str, object],
    ) -> dict[str, object]:
        assert capability_id == "change_one"
        return {"approval_token": "approved-token"}

    async def execute(
        self,
        payload: dict[str, object],
        _http: object,
        *,
        device_id: str,
    ) -> dict[str, object]:
        request_id = str(payload["request_id"])
        capability_id = str(payload["capability_id"])
        self.tasks[request_id] = asyncio.current_task()  # type: ignore[assignment]
        self.calls.append((device_id, capability_id))
        self.started.set()
        delay = float(payload["arguments"].get("delay", 0))  # type: ignore[union-attr]
        if delay:
            await asyncio.sleep(delay)
        side_effect = capability_id == "change_one"
        if side_effect:
            assert payload.get("approval_token") == "approved-token"
        return {
            "ok": True,
            "side_effect": side_effect,
            "read_only": not side_effect,
            "data": {"verified": True} if side_effect else {"value": capability_id},
            "evidence": [{"source": "test"}],
        }

    async def cancel(self, _device_id: str, request_id: str) -> bool:
        task = self.tasks.get(request_id)
        if task is None:
            return False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True


def run(coro):
    return asyncio.run(coro)


async def wait_for_status(manager: AgentRunManager, expected: str) -> dict[str, object]:
    for _ in range(100):
        status = await manager.status("reachy", CONTEXT)
        assert status is not None
        if status["status"] == expected:
            return status
        await asyncio.sleep(0.005)
    raise AssertionError(f"run never reached {expected}")


def test_plan_is_exact_bounded_and_rejects_replacement_while_active() -> None:
    async def scenario() -> None:
        manager = AgentRunManager()
        plan = await manager.create(
            device_id="reachy",
            context=CONTEXT,
            goal="Read both sources",
            planned_calls=[
                {"capability_id": "read_one", "arguments": {}},
                {"capability_id": "read_two", "arguments": {"value": "x"}},
            ],
            manifest=MANIFEST,
            validate_arguments=validate,
        )
        assert plan["status"] == "preview"
        assert [step["status"] for step in plan["steps"]] == ["queued", "queued"]
        assert plan["budgets"] == {
            "max_steps": 5,
            "max_tool_calls": 5,
            "max_side_effects": 2,
            "max_seconds": 120.0,
            "heartbeat_seconds": 15.0,
        }
        with pytest.raises(AgentRunValidationError, match="already active"):
            await manager.create(
                device_id="reachy",
                context=CONTEXT,
                goal="Replacement",
                planned_calls=[{"capability_id": "read_one", "arguments": {}}],
                manifest=MANIFEST,
                validate_arguments=validate,
            )
        cancelled = await manager.cancel(
            "reachy", CONTEXT, str(plan["run_id"]), Broker()
        )
        assert cancelled["status"] == "cancelled"
        assert cancelled["steps"][0]["status"] == "cancelled"

    run(scenario())


def test_new_generation_replaces_a_nonrunning_stale_preview() -> None:
    async def scenario() -> None:
        manager = AgentRunManager()
        old = await manager.create(
            device_id="reachy",
            context=CONTEXT,
            goal="Old preview",
            planned_calls=[{"capability_id": "read_one", "arguments": {}}],
            manifest=MANIFEST,
            validate_arguments=validate,
        )
        new_context = {
            **CONTEXT,
            "session_generation": 8,
            "requested_session_generation": 8,
        }
        new = await manager.create(
            device_id="reachy",
            context=new_context,
            goal="Fresh preview",
            planned_calls=[{"capability_id": "read_two", "arguments": {}}],
            manifest=MANIFEST,
            validate_arguments=validate,
        )
        assert new["run_id"] != old["run_id"]
        assert new["generation"] == 8
        assert new["status"] == "preview"

    run(scenario())


def test_plan_rejects_step_side_effect_and_schema_budget_violations() -> None:
    async def scenario() -> None:
        manager = AgentRunManager(budgets=RunBudgets(max_steps=2, max_side_effects=1))
        with pytest.raises(AgentRunValidationError, match="step budget"):
            await manager.create(
                device_id="reachy",
                context=CONTEXT,
                goal="Too many",
                planned_calls=[{"capability_id": "read_one", "arguments": {}}] * 3,
                manifest=MANIFEST,
                validate_arguments=validate,
            )
        with pytest.raises(AgentRunValidationError, match="side-effect budget"):
            await manager.create(
                device_id="reachy",
                context=CONTEXT,
                goal="Too many changes",
                planned_calls=[
                    {"capability_id": "change_one", "arguments": {}},
                    {"capability_id": "change_one", "arguments": {}},
                ],
                manifest=MANIFEST,
                validate_arguments=validate,
            )
        with pytest.raises(ValueError, match="extra argument"):
            await manager.create(
                device_id="reachy",
                context=CONTEXT,
                goal="Bad args",
                planned_calls=[{"capability_id": "read_one", "arguments": {"nope": 1}}],
                manifest=MANIFEST,
                validate_arguments=validate,
            )

    run(scenario())


def test_read_only_run_completes_with_per_step_progress() -> None:
    async def scenario() -> None:
        manager = AgentRunManager()
        broker = Broker()
        plan = await manager.create(
            device_id="reachy",
            context=CONTEXT,
            goal="Read both",
            planned_calls=[
                {"capability_id": "read_one", "arguments": {}},
                {"capability_id": "read_two", "arguments": {}},
            ],
            manifest=MANIFEST,
            validate_arguments=validate,
        )
        await manager.start("reachy", CONTEXT, str(plan["run_id"]), broker, object())
        completed = await wait_for_status(manager, "completed")
        assert completed["tool_calls_used"] == 2
        assert completed["side_effects_used"] == 0
        assert [step["status"] for step in completed["steps"]] == ["completed", "completed"]
        assert broker.calls == [("reachy", "read_one"), ("reachy", "read_two")]

    run(scenario())


def test_approval_step_pauses_before_execution_then_continues_once() -> None:
    async def scenario() -> None:
        manager = AgentRunManager()
        broker = Broker()
        plan = await manager.create(
            device_id="reachy",
            context=CONTEXT,
            goal="Read then change",
            planned_calls=[
                {"capability_id": "read_one", "arguments": {}},
                {"capability_id": "change_one", "arguments": {"value": "exact"}},
            ],
            manifest=MANIFEST,
            validate_arguments=validate,
        )
        run_id = str(plan["run_id"])
        await manager.start("reachy", CONTEXT, run_id, broker, object())
        waiting = await wait_for_status(manager, "waiting_approval")
        assert broker.calls == [("reachy", "read_one")]
        assert waiting["active_step_id"] == "step-2"
        paused = await manager.pause("reachy", CONTEXT, run_id, broker)
        assert paused["status"] == "paused"
        resumed = await manager.start("reachy", CONTEXT, run_id, broker, object())
        assert resumed["status"] == "waiting_approval"
        assert broker.calls == [("reachy", "read_one")]
        await manager.approve("reachy", CONTEXT, run_id, "step-2", broker, object())
        completed = await wait_for_status(manager, "completed")
        assert completed["side_effects_used"] == 1
        assert broker.calls == [("reachy", "read_one"), ("reachy", "change_one")]
        with pytest.raises(AgentRunValidationError, match="not awaiting"):
            await manager.approve("reachy", CONTEXT, run_id, "step-2", broker, object())

    run(scenario())


def test_pause_and_resume_are_generation_and_context_bound() -> None:
    async def scenario() -> None:
        manager = AgentRunManager()
        broker = Broker()
        plan = await manager.create(
            device_id="reachy",
            context=CONTEXT,
            goal="Slow safe read",
            planned_calls=[{"capability_id": "read_one", "arguments": {"delay": 5}}],
            manifest=MANIFEST,
            validate_arguments=validate,
        )
        run_id = str(plan["run_id"])
        await manager.start("reachy", CONTEXT, run_id, broker, object())
        await broker.started.wait()
        paused = await manager.pause("reachy", CONTEXT, run_id, broker)
        assert paused["status"] == "paused"
        assert paused["steps"][0]["status"] == "queued"
        stale = {**CONTEXT, "session_generation": 8, "requested_session_generation": 8}
        with pytest.raises(AgentRunValidationError, match="stale_or_changed"):
            await manager.start("reachy", stale, run_id, broker, object())
        broker.started = asyncio.Event()
        await manager.start("reachy", CONTEXT, run_id, broker, object())
        task = manager._runs["reachy"].task
        assert task is not None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    run(scenario())


def test_stop_during_possible_side_effect_is_non_resumable_and_uncertain() -> None:
    async def scenario() -> None:
        manager = AgentRunManager()
        broker = Broker()
        plan = await manager.create(
            device_id="reachy",
            context=CONTEXT,
            goal="Approved slow change",
            planned_calls=[{"capability_id": "change_one", "arguments": {"delay": 5}}],
            manifest=MANIFEST,
            validate_arguments=validate,
        )
        run_id = str(plan["run_id"])
        await manager.start("reachy", CONTEXT, run_id, broker, object())
        await wait_for_status(manager, "waiting_approval")
        await manager.approve("reachy", CONTEXT, run_id, "step-1", broker, object())
        await broker.started.wait()
        stopped = await manager.cancel("reachy", CONTEXT, run_id, broker)
        assert stopped["status"] == "cancelled"
        assert stopped["resumable"] is False
        assert stopped["steps"][0]["status"] == "uncertain"
        with pytest.raises(AgentRunValidationError):
            await manager.start("reachy", CONTEXT, run_id, broker, object())

    run(scenario())


def test_missing_heartbeat_blocks_the_next_step() -> None:
    async def scenario() -> None:
        now = [0.0]
        manager = AgentRunManager(clock=lambda: now[0])

        class HeartbeatBroker(Broker):
            async def execute(self, payload, http, *, device_id):
                result = await super().execute(payload, http, device_id=device_id)
                now[0] = 20.0
                return result

        broker = HeartbeatBroker()
        plan = await manager.create(
            device_id="reachy",
            context=CONTEXT,
            goal="Do not continue after the phone disappears",
            planned_calls=[
                {"capability_id": "read_one", "arguments": {}},
                {"capability_id": "read_two", "arguments": {}},
            ],
            manifest=MANIFEST,
            validate_arguments=validate,
        )
        await manager.start("reachy", CONTEXT, str(plan["run_id"]), broker, object())
        task = manager._runs["reachy"].task
        assert task is not None
        await task
        failed = await manager.status("reachy", CONTEXT)
        assert failed is not None and failed["status"] == "partial"
        assert broker.calls == [("reachy", "read_one")]
        assert failed["steps"][1]["error_class"] == "heartbeat_expired"

    run(scenario())


def test_unverified_side_effect_is_partial_uncertain_and_non_resumable() -> None:
    async def scenario() -> None:
        manager = AgentRunManager()

        class UnverifiedBroker(Broker):
            async def execute(self, payload, http, *, device_id):
                result = await super().execute(payload, http, device_id=device_id)
                if result["side_effect"] is True:
                    result["data"] = {"verified": False}
                return result

        broker = UnverifiedBroker()
        plan = await manager.create(
            device_id="reachy",
            context=CONTEXT,
            goal="Reject unverified completion",
            planned_calls=[{"capability_id": "change_one", "arguments": {}}],
            manifest=MANIFEST,
            validate_arguments=validate,
        )
        run_id = str(plan["run_id"])
        await manager.start("reachy", CONTEXT, run_id, broker, object())
        await wait_for_status(manager, "waiting_approval")
        await manager.approve("reachy", CONTEXT, run_id, "step-1", broker, object())
        partial = await wait_for_status(manager, "partial")
        assert partial["steps"][0]["status"] == "uncertain"
        assert partial["steps"][0]["result_class"] == "unverified_side_effect"
        assert partial["resumable"] is False

    run(scenario())
