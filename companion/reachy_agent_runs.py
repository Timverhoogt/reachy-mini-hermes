"""Generation-bound bounded multi-step runs for Reachy Agent 0.5.

Runs are deliberately in-memory. A bridge restart loses every checkpoint and can
never resume a side effect automatically. The trusted phone UI must preview an
exact plan before Start and approve every approval-gated step separately.
"""

from __future__ import annotations

import asyncio
import copy
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

_TERMINAL = frozenset({"completed", "partial", "failed", "cancelled"})
_STEP_TERMINAL = frozenset({"completed", "failed", "cancelled", "uncertain"})
_RUN_DISALLOWED_CAPABILITIES = frozenset(
    {"draft_calendar_event", "draft_message", "draft_note"}
)
_CONTEXT_KEYS = (
    "capability_profile",
    "adult_ui_unlocked",
    "kids_mode_active",
    "power_mode",
    "privacy_enabled",
    "emergency_stop_active",
    "robot_available",
    "session_generation",
    "requested_session_generation",
)


class AgentRunValidationError(ValueError):
    """A plan or run transition violated the bounded Agent 0.5 contract."""


@dataclass(frozen=True, slots=True)
class RunBudgets:
    max_steps: int = 5
    max_tool_calls: int = 5
    max_side_effects: int = 2
    max_seconds: float = 120.0
    heartbeat_seconds: float = 15.0


@dataclass(slots=True)
class RunStep:
    step_id: str
    title: str
    capability_id: str
    arguments: dict[str, object]
    risk_tier: str
    expected_side_effect: bool
    requires_approval: bool
    status: str = "queued"
    result_class: str = ""
    error_class: str = ""
    evidence_sources: list[str] = field(default_factory=list)
    verified: bool = False
    started_at: float | None = None
    completed_at: float | None = None
    approval_token: str = ""

    def public(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "capability_id": self.capability_id,
            "arguments": copy.deepcopy(self.arguments),
            "risk_tier": self.risk_tier,
            "expected_side_effect": self.expected_side_effect,
            "requires_approval": self.requires_approval,
            "status": self.status,
            "result_class": self.result_class,
            "error_class": self.error_class,
            "evidence_sources": list(self.evidence_sources),
            "verified": self.verified,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(slots=True)
class AgentRun:
    run_id: str
    device_id: str
    generation: int
    goal: str
    context_state: tuple[object, ...]
    steps: list[RunStep]
    budgets: RunBudgets
    status: str = "preview"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    started_monotonic: float | None = field(default=None, repr=False)
    completed_at: float | None = None
    active_step_id: str = ""
    active_request_id: str = ""
    tool_calls_used: int = 0
    side_effects_used: int = 0
    pause_requested: bool = False
    cancel_requested: bool = False
    resumable: bool = True
    last_heartbeat: float = 0.0
    latest_context: Any = field(default=None, repr=False)
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def public(self) -> dict[str, object]:
        completed = sum(step.status == "completed" for step in self.steps)
        failed = sum(step.status == "failed" for step in self.steps)
        uncertain = sum(step.status == "uncertain" for step in self.steps)
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "status": self.status,
            "generation": self.generation,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "active_step_id": self.active_step_id,
            "tool_calls_used": self.tool_calls_used,
            "side_effects_used": self.side_effects_used,
            "resumable": self.resumable,
            "summary": (
                f"{completed}/{len(self.steps)} steps completed; "
                f"{self.side_effects_used} verified side effects; "
                f"{failed} failed; {uncertain} uncertain"
            ),
            "budgets": {
                "max_steps": self.budgets.max_steps,
                "max_tool_calls": self.budgets.max_tool_calls,
                "max_side_effects": self.budgets.max_side_effects,
                "max_seconds": self.budgets.max_seconds,
                "heartbeat_seconds": self.budgets.heartbeat_seconds,
            },
            "steps": [step.public() for step in self.steps],
        }


class AgentRunManager:
    """Own one observable, interruptible run per Reachy device."""

    def __init__(self, *, budgets: RunBudgets | None = None, clock=time.monotonic) -> None:
        self.budgets = budgets or RunBudgets()
        self._clock = clock
        self._runs: dict[str, AgentRun] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _context_state(context: Any) -> tuple[object, ...]:
        def value(name: str) -> object:
            if isinstance(context, dict):
                return context.get(name)
            return getattr(context, name, None)

        return tuple(value(name) for name in _CONTEXT_KEYS)

    @staticmethod
    def _generation(context: Any) -> int:
        value = context.get("session_generation") if isinstance(context, dict) else getattr(
            context, "session_generation", None
        )
        if type(value) is not int:
            raise AgentRunValidationError("invalid Agent run generation")
        return value

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not re.fullmatch(r"run-[0-9a-f]{24}", run_id):
            raise AgentRunValidationError("invalid run_id")

    def _assert_context(self, run: AgentRun, context: Any) -> None:
        if self._generation(context) != run.generation or self._context_state(context) != run.context_state:
            raise AgentRunValidationError("stale_or_changed_run_context")

    async def create(
        self,
        *,
        device_id: str,
        context: Any,
        goal: str,
        planned_calls: list[dict[str, object]],
        manifest: list[dict[str, object]],
        validate_arguments: Any,
    ) -> dict[str, object]:
        clean_goal = goal.strip()
        if not clean_goal or len(clean_goal) > 2_000:
            raise AgentRunValidationError("Agent run goal must contain 1-2000 characters")
        if not 1 <= len(planned_calls) <= self.budgets.max_steps:
            raise AgentRunValidationError("Agent run exceeds the step budget")
        specs = {str(item.get("id")): item for item in manifest}
        steps: list[RunStep] = []
        side_effect_count = 0
        for index, call in enumerate(planned_calls, start=1):
            if not isinstance(call, dict) or set(call) != {"capability_id", "arguments"}:
                raise AgentRunValidationError("planned step has an invalid shape")
            capability_id = str(call["capability_id"])
            spec = specs.get(capability_id)
            arguments = call["arguments"]
            if spec is None or not isinstance(arguments, dict):
                raise AgentRunValidationError("planned step uses an unknown capability")
            if capability_id in _RUN_DISALLOWED_CAPABILITIES:
                raise AgentRunValidationError(
                    "Agent run preview is already the exact draft; use the approval-gated target capability"
                )
            validate_arguments(capability_id, arguments)
            expected_side_effect = not bool(spec.get("read_only", True))
            side_effect_count += int(expected_side_effect)
            title = str(spec.get("description") or capability_id.replace("_", " "))[:180]
            steps.append(
                RunStep(
                    step_id=f"step-{index}",
                    title=title,
                    capability_id=capability_id,
                    arguments=dict(arguments),
                    risk_tier=str(spec.get("risk_tier") or ""),
                    expected_side_effect=expected_side_effect,
                    requires_approval=bool(spec.get("requires_approval")),
                )
            )
        if side_effect_count > self.budgets.max_side_effects:
            raise AgentRunValidationError("Agent run exceeds the side-effect budget")
        generation = self._generation(context)
        run = AgentRun(
            run_id=f"run-{uuid.uuid4().hex[:24]}",
            device_id=device_id,
            generation=generation,
            goal=clean_goal,
            context_state=self._context_state(context),
            steps=steps,
            budgets=self.budgets,
            last_heartbeat=self._clock(),
            latest_context=context,
        )
        async with self._lock:
            previous = self._runs.get(device_id)
            if previous is not None and previous.status not in _TERMINAL:
                if previous.generation != generation:
                    self._mark_cancelled(previous)
                else:
                    raise AgentRunValidationError("another Agent run is already active")
            self._runs[device_id] = run
            return run.public()

    async def status(self, device_id: str, context: Any, run_id: str = "") -> dict[str, object] | None:
        async with self._lock:
            run = self._runs.get(device_id)
            if run is None:
                return None
            if run_id:
                self._validate_run_id(run_id)
                if run.run_id != run_id:
                    raise AgentRunValidationError("Agent run was not found")
            self._assert_context(run, context)
            if run.status not in _TERMINAL:
                run.last_heartbeat = self._clock()
                run.latest_context = context
            return run.public()

    async def start(self, device_id: str, context: Any, run_id: str, broker: Any, http: Any) -> dict[str, object]:
        self._validate_run_id(run_id)
        async with self._lock:
            run = self._runs.get(device_id)
            if run is None or run.run_id != run_id:
                raise AgentRunValidationError("Agent run was not found")
            self._assert_context(run, context)
            if run.status not in {"preview", "paused"}:
                raise AgentRunValidationError("Agent run cannot start from its current state")
            if run.status == "paused" and not run.resumable:
                raise AgentRunValidationError("Agent run is not safely resumable")
            waiting_step = next(
                (step for step in run.steps if step.status == "waiting_approval"), None
            )
            if waiting_step is not None:
                run.pause_requested = False
                run.cancel_requested = False
                run.last_heartbeat = self._clock()
                run.latest_context = context
                run.status = "waiting_approval"
                run.active_step_id = waiting_step.step_id
                return run.public()
            run.pause_requested = False
            run.cancel_requested = False
            run.last_heartbeat = self._clock()
            run.latest_context = context
            run.status = "running"
            run.started_at = run.started_at or time.time()
            if run.started_monotonic is None:
                run.started_monotonic = self._clock()
            run.task = asyncio.create_task(self._drive(run, context, broker, http))
            return run.public()

    async def approve(
        self,
        device_id: str,
        context: Any,
        run_id: str,
        step_id: str,
        broker: Any,
        http: Any,
    ) -> dict[str, object]:
        self._validate_run_id(run_id)
        async with self._lock:
            run = self._runs.get(device_id)
            if run is None or run.run_id != run_id:
                raise AgentRunValidationError("Agent run was not found")
            self._assert_context(run, context)
            step = next((item for item in run.steps if item.step_id == step_id), None)
            if run.status != "waiting_approval" or step is None or step.status != "waiting_approval":
                raise AgentRunValidationError("Agent run step is not awaiting approval")
        approval = await broker.issue_approval(
            device_id, context, step.capability_id, step.arguments
        )
        token = approval.get("approval_token")
        if not isinstance(token, str) or not token:
            raise AgentRunValidationError("Agent run approval token was not issued")
        async with self._lock:
            self._assert_context(run, context)
            if (
                run.status != "waiting_approval"
                or step.status != "waiting_approval"
                or run.cancel_requested
            ):
                raise AgentRunValidationError("Agent run approval was invalidated")
            run.last_heartbeat = self._clock()
            run.latest_context = context
            step.approval_token = token
            step.status = "queued"
            run.status = "running"
            run.task = asyncio.create_task(self._drive(run, context, broker, http))
            return run.public()

    async def pause(self, device_id: str, context: Any, run_id: str, broker: Any) -> dict[str, object]:
        return await self._interrupt(device_id, context, run_id, broker, cancel=False)

    async def cancel(self, device_id: str, context: Any, run_id: str, broker: Any) -> dict[str, object]:
        return await self._interrupt(device_id, context, run_id, broker, cancel=True)

    async def _interrupt(
        self, device_id: str, context: Any, run_id: str, broker: Any, *, cancel: bool
    ) -> dict[str, object]:
        self._validate_run_id(run_id)
        request_id = ""
        task: asyncio.Task[None] | None = None
        async with self._lock:
            run = self._runs.get(device_id)
            if run is None or run.run_id != run_id:
                raise AgentRunValidationError("Agent run was not found")
            self._assert_context(run, context)
            if run.status in _TERMINAL:
                return run.public()
            run.cancel_requested = cancel
            run.pause_requested = not cancel
            request_id = run.active_request_id
            task = run.task
            if not request_id and run.status == "waiting_approval":
                if cancel:
                    self._mark_cancelled(run)
                else:
                    run.status = "paused"
                return run.public()
            if not request_id and (task is None or task.done()):
                if cancel:
                    self._mark_cancelled(run)
                else:
                    run.status = "paused"
                return run.public()
        if request_id:
            await broker.cancel(device_id, request_id)
        elif task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self._lock:
            return run.public()

    async def _drive(self, run: AgentRun, context: Any, broker: Any, http: Any) -> None:
        try:
            while True:
                async with self._lock:
                    if run.cancel_requested:
                        self._mark_cancelled(run)
                        return
                    if run.pause_requested:
                        run.status = "paused"
                        return
                    if run.tool_calls_used >= run.budgets.max_tool_calls:
                        self._fail_budget(run, "tool_call_budget_exceeded")
                        return
                    started_monotonic = run.started_monotonic
                    if started_monotonic is None or self._clock() - started_monotonic > run.budgets.max_seconds:
                        self._fail_budget(run, "time_budget_exceeded")
                        return
                    if self._clock() - run.last_heartbeat > run.budgets.heartbeat_seconds:
                        self._fail_budget(run, "heartbeat_expired")
                        return
                    step = next((item for item in run.steps if item.status == "queued"), None)
                    if step is None:
                        failures = any(item.status in {"failed", "uncertain"} for item in run.steps)
                        run.status = "partial" if failures else "completed"
                        run.completed_at = time.time()
                        run.active_step_id = ""
                        run.active_request_id = ""
                        return
                    if step.requires_approval and not step.approval_token:
                        step.status = "waiting_approval"
                        run.status = "waiting_approval"
                        run.active_step_id = step.step_id
                        run.active_request_id = ""
                        return
                    request_id = f"runstep-{uuid.uuid4().hex[:24]}"
                    step.status = "running"
                    step.started_at = time.time()
                    run.active_step_id = step.step_id
                    run.active_request_id = request_id
                    run.tool_calls_used += 1
                    execution_context = run.latest_context
                await broker.assert_current(run.device_id, run.generation)
                result = await broker.execute(
                    {
                        "request_id": request_id,
                        "capability_id": step.capability_id,
                        "arguments": step.arguments,
                        "context": dict(execution_context) if isinstance(execution_context, dict) else {
                            name: getattr(execution_context, name) for name in _CONTEXT_KEYS
                        } | {
                            "explicit_private_intent": True,
                            "reachy_status": getattr(execution_context, "reachy_status", {}),
                        },
                        **({"approval_token": step.approval_token} if step.approval_token else {}),
                    },
                    http,
                    device_id=run.device_id,
                )
                async with self._lock:
                    self._assert_context(run, context)
                    step.status = "completed"
                    step.result_class = "success"
                    step.completed_at = time.time()
                    evidence = result.get("evidence")
                    if isinstance(evidence, list):
                        step.evidence_sources = sorted(
                            {
                                str(item.get("source"))[:80]
                                for item in evidence
                                if isinstance(item, dict) and item.get("source")
                            }
                        )[:8]
                    step.verified = (
                        result.get("side_effect") is not True
                        and isinstance(evidence, list)
                        and bool(evidence)
                    )
                    run.active_step_id = ""
                    run.active_request_id = ""
                    if result.get("side_effect") is not True and not step.verified:
                        step.status = "failed"
                        step.result_class = "missing_evidence"
                        run.status = "partial" if any(
                            item.status == "completed" for item in run.steps
                        ) else "failed"
                        self._cancel_queued(run, "blocked_after_failure")
                        run.completed_at = time.time()
                        return
                    if result.get("side_effect") is True:
                        data = result.get("data")
                        if not isinstance(data, dict) or data.get("verified") is not True:
                            step.status = "uncertain"
                            step.result_class = "unverified_side_effect"
                            run.status = "partial"
                            run.resumable = False
                            self._cancel_queued(run, "blocked_after_uncertain_side_effect")
                            run.completed_at = time.time()
                            return
                        step.verified = True
                        run.side_effects_used += 1
                        if run.side_effects_used > run.budgets.max_side_effects:
                            self._fail_budget(run, "side_effect_budget_exceeded")
                            return
        except asyncio.CancelledError:
            async with self._lock:
                step = next((item for item in run.steps if item.step_id == run.active_step_id), None)
                if step is not None and step.status == "running":
                    if step.expected_side_effect:
                        step.status = "uncertain"
                        step.result_class = "cancelled_during_side_effect"
                        run.resumable = False
                    elif run.cancel_requested:
                        step.status = "cancelled"
                        step.result_class = "cancelled"
                    else:
                        step.status = "queued"
                        step.started_at = None
                run.active_step_id = ""
                run.active_request_id = ""
                if run.cancel_requested:
                    self._mark_cancelled(run)
                elif run.resumable:
                    run.status = "paused"
                else:
                    run.status = "partial"
                    run.completed_at = time.time()
        except Exception as exc:
            async with self._lock:
                step = next((item for item in run.steps if item.step_id == run.active_step_id), None)
                if step is not None:
                    step.status = "uncertain" if step.expected_side_effect else "failed"
                    step.result_class = (
                        "side_effect_not_verified" if step.expected_side_effect else "failed"
                    )
                    step.error_class = type(exc).__name__[:80]
                    step.completed_at = time.time()
                    if step.expected_side_effect:
                        run.resumable = False
                run.active_step_id = ""
                run.active_request_id = ""
                run.status = "partial" if (
                    step is not None and step.expected_side_effect
                ) or any(item.status == "completed" for item in run.steps) else "failed"
                self._cancel_queued(run, "blocked_after_failure")
                run.completed_at = time.time()

    async def shutdown(self) -> None:
        """Cancel every in-memory run; nothing is persisted or auto-resumed."""
        async with self._lock:
            runs = list(self._runs.values())
            tasks = [run.task for run in runs if run.task is not None and not run.task.done()]
            for run in runs:
                run.cancel_requested = True
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            for run in runs:
                if run.status not in _TERMINAL:
                    self._mark_cancelled(run)

    @staticmethod
    def _cancel_queued(run: AgentRun, reason: str) -> None:
        for step in run.steps:
            if step.status == "queued":
                step.status = "cancelled"
                step.result_class = reason

    @staticmethod
    def _mark_cancelled(run: AgentRun) -> None:
        for step in run.steps:
            if step.status in {"queued", "waiting_approval"}:
                step.status = "cancelled"
                step.result_class = "cancelled"
        run.status = "cancelled"
        run.resumable = False
        run.active_step_id = ""
        run.active_request_id = ""
        run.completed_at = time.time()

    @staticmethod
    def _fail_budget(run: AgentRun, reason: str) -> None:
        for step in run.steps:
            if step.status == "queued":
                step.status = "cancelled"
                step.result_class = "budget_exhausted"
        run.status = "partial" if any(step.status == "completed" for step in run.steps) else "failed"
        run.resumable = False
        run.active_step_id = ""
        run.active_request_id = ""
        run.completed_at = time.time()
        if run.steps:
            target = next((step for step in run.steps if step.status == "cancelled"), run.steps[-1])
            target.error_class = reason
