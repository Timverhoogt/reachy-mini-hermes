from __future__ import annotations

import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

import pytest

from reachy_mini_hermes.config import AppConfig
from reachy_mini_hermes.presence import PresenceObservation
from reachy_mini_hermes.runtime import HermesVoiceRuntime


class Motion:
    enabled = True

    def __init__(self) -> None:
        self.resume_calls = 0

    def resume(self) -> None:
        self.resume_calls += 1


class Actions:
    busy = False
    pending_count = 0

    def __init__(self) -> None:
        self.queued: list[tuple[str, dict[str, object], bool, bool]] = []

    def enqueue(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        hold_pose: bool = False,
        reject_if_busy: bool = False,
    ) -> dict[str, object]:
        self.queued.append((name, arguments, hold_pose, reject_if_busy))
        return {"accepted": True, "queued": name}


class Robot:
    media = SimpleNamespace()


def make_runtime() -> tuple[HermesVoiceRuntime, Motion, Actions]:
    config = AppConfig(
        proactive_presence_enabled=True,
        presence_acknowledgement_enabled=True,
    )
    runtime = HermesVoiceRuntime(Robot(), threading.Event(), config_loader=lambda: config)
    motion = Motion()
    actions = Actions()
    runtime._motion = motion  # type: ignore[assignment]
    runtime._actions = actions  # type: ignore[assignment]
    runtime._control_ready.set()
    runtime._power_mode = "awake"
    runtime._motors_enabled = True
    runtime._status.state = "waiting_for_wake_word"
    return runtime, motion, actions


def signal() -> PresenceObservation:
    return PresenceObservation(
        source="home_assistant",
        occupied=True,
        attentive=False,
        direction_degrees=18.0,
        confidence=0.9,
    )


def test_safe_awake_presence_queues_cancellable_action_then_obeys_cooldown() -> None:
    runtime, motion, actions = make_runtime()

    queued = runtime.observe_presence(signal())

    assert actions.queued == [
        ("acknowledge_presence", {"direction_degrees": 18.0}, True, True)
    ]
    assert queued["last_outcome"] == "acknowledgement_queued"
    assert queued["acknowledgements"] == 0
    assert runtime._presence_action_active.is_set()

    runtime._after_robot_action()
    runtime._on_robot_action_result(
        "acknowledge_presence",
        {"ok": True, "action": "acknowledge_presence"},
    )
    completed = cast(dict[str, object], runtime.status()["presence"])
    second = runtime.observe_presence(signal())

    assert motion.resume_calls == 1
    assert completed["last_outcome"] == "acknowledged_silently"
    assert completed["acknowledgements"] == 1
    assert second["last_outcome"] == "cooldown"
    assert second["speech_enabled"] is False
    assert len(actions.queued) == 1


def test_cancelled_presence_action_is_not_counted_and_releases_motion_owner() -> None:
    runtime, motion, _actions = make_runtime()
    runtime.observe_presence(signal())

    runtime._on_robot_action_result(
        "acknowledge_presence",
        {
            "ok": False,
            "error": "Robot action was cancelled",
            "action": "acknowledge_presence",
        },
    )
    payload = cast(dict[str, object], runtime.status()["presence"])

    assert runtime._presence_action_active.is_set() is False
    assert motion.resume_calls == 1
    assert payload["last_outcome"] == "acknowledgement_cancelled"
    assert payload["acknowledgements"] == 0


def test_kids_transition_rechecks_at_presence_action_execution_time() -> None:
    runtime, _motion, _actions = make_runtime()
    queued = runtime.observe_presence(signal())
    assert queued["last_outcome"] == "acknowledgement_queued"
    runtime._kids_active = True

    with pytest.raises(RuntimeError, match="Kids Mode"):
        runtime._before_robot_action()


def test_presence_never_wakes_standby_or_enables_motors() -> None:
    runtime, _motion, actions = make_runtime()
    runtime._power_mode = "standby"
    runtime._motors_enabled = False
    motor_changes: list[bool] = []
    runtime._set_motor_mode = lambda enabled, wake=False: motor_changes.append(enabled)  # type: ignore[method-assign]

    payload = runtime.observe_presence(signal())

    assert actions.queued == []
    assert motor_changes == []
    assert runtime._power_mode == "standby"
    assert runtime._motors_enabled is False
    assert payload["level"] == "present"
    assert payload["last_outcome"] == "not_awake"


def test_disabled_presence_discards_the_signal_and_never_queues() -> None:
    config = AppConfig(proactive_presence_enabled=False)
    runtime = HermesVoiceRuntime(Robot(), threading.Event(), config_loader=lambda: config)
    actions = Actions()
    runtime._actions = actions  # type: ignore[assignment]

    payload = runtime.observe_presence(signal())

    assert actions.queued == []
    assert payload["enabled"] is False
    assert payload["level"] == "away"
    assert payload["source"] is None


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda runtime: runtime._privacy_requested.set(), "privacy"),
        (lambda runtime: setattr(runtime, "_motors_enabled", False), "motors_not_enabled"),
        (lambda runtime: setattr(runtime, "_kids_active", True), "kids_mode"),
        (lambda runtime: runtime._announcement_active.set(), "announcement_active"),
        (lambda runtime: setattr(runtime._status, "state", "listening"), "voice_active"),
        (lambda runtime: setattr(runtime, "_camera_control_session_id", "camera-test"), "camera_control_active"),
        (lambda runtime: setattr(runtime, "_face_tracking_active", True), "face_tracking_active"),
        (
            lambda runtime: setattr(runtime, "_actions", SimpleNamespace(busy=True, pending_count=1)),
            "robot_action_active",
        ),
    ],
)
def test_presence_motion_is_suppressed_by_every_active_owner(
    mutate: Callable[[HermesVoiceRuntime], None], reason: str
) -> None:
    runtime, _motion, actions = make_runtime()
    mutate(runtime)

    payload = runtime.observe_presence(signal())

    assert actions.queued == []
    assert payload["last_outcome"] == reason
    assert payload["acknowledgements"] == 0


def test_presence_motion_does_not_wait_behind_active_voice_owner() -> None:
    runtime, _motion, actions = make_runtime()
    runtime._voice_activity_lock.acquire()
    try:
        payload = runtime.observe_presence(signal())
    finally:
        runtime._voice_activity_lock.release()

    assert actions.queued == []
    assert payload["last_outcome"] == "voice_active"


def test_internal_voice_observation_updates_attention_without_second_motion() -> None:
    runtime, _motion, actions = make_runtime()

    payload = runtime.observe_presence(
        PresenceObservation(source="voice", occupied=True, attentive=True),
        allow_acknowledgement=False,
    )

    assert actions.queued == []
    assert payload["level"] == "attentive"
    assert payload["source"] == "voice"
    assert payload["last_outcome"] == "observed_voice"


def test_kids_locked_status_does_not_expose_presence_state() -> None:
    runtime, _motion, _actions = make_runtime()
    runtime.observe_presence(signal())
    runtime._kids_locked = True

    payload = runtime.status()

    assert "presence" not in payload
    assert set(payload) == {
        "state",
        "power_mode",
        "motors_enabled",
        "head_safely_folded",
        "kids_mode",
    }
