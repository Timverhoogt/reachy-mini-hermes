from __future__ import annotations

import threading

import pytest

from reachy_mini_hermes.robot_tools import (
    ReachyRobotActions,
    completed_robot_tool_call,
    manual_robot_action,
    robot_control_options,
)


class FakeMove:
    duration = 0.04

    def evaluate(self, elapsed: float) -> tuple[dict[str, float], list[float], float]:
        return {"elapsed": elapsed}, [elapsed, -elapsed], elapsed


class FakeLibrary:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def get(self, name: str) -> FakeMove:
        self.requested.append(name)
        return FakeMove()


class FakeRobot:
    def __init__(self) -> None:
        self.targets: list[dict[str, object]] = []
        self.head_samples: list[object] = []
        self.body_samples: list[float] = []
        self.antenna_samples: list[list[float]] = []
        self.cancellations = 0

    def get_current_head_pose(self) -> dict[str, bool]:
        return {"current": True}

    def goto_target(self, **kwargs: object) -> None:
        self.targets.append(kwargs)

    def set_target_head_pose(self, head: object) -> None:
        self.head_samples.append(head)

    def set_target_body_yaw(self, body_yaw: float) -> None:
        self.body_samples.append(body_yaw)

    def set_target_antenna_joint_positions(self, positions: list[float]) -> None:
        self.antenna_samples.append(positions)

    def cancel_move(self) -> None:
        self.cancellations += 1


def test_completed_robot_tool_call_requires_completed_output_item() -> None:
    payload = {
        "item": {
            "type": "function_call",
            "name": "move_reachy_head",
            "status": "completed",
            "call_id": "call-look",
            "arguments": '{"direction":"left"}',
        }
    }

    call = completed_robot_tool_call("response.output_item.done", payload)

    assert call is not None
    assert call.call_id == "call-look"
    assert call.name == "move_reachy_head"
    assert call.arguments == {"direction": "left"}
    assert completed_robot_tool_call("response.function_call_arguments.done", payload) is None


def test_manual_robot_controls_translate_only_curated_values() -> None:
    assert manual_robot_action("look", "left") == ("move_reachy_head", {"direction": "left"})
    assert manual_robot_action("emotion", "happy") == (
        "express_reachy_emotion",
        {"emotion": "happy"},
    )
    assert manual_robot_action("dance", "groovy") == ("dance_reachy", {"style": "groovy"})
    assert robot_control_options()["look"] == ["left", "right", "up", "down", "center"]
    with pytest.raises(ValueError):
        manual_robot_action("joint", "neck=180")


def test_robot_actions_use_safe_curated_moves_without_move_audio(monkeypatch) -> None:
    monkeypatch.setattr(
        "reachy_mini_hermes.robot_tools.create_head_pose",
        lambda **kwargs: kwargs,
    )
    robot = FakeRobot()
    library = FakeLibrary()
    actions = ReachyRobotActions(
        robot,
        threading.Event(),
        library_factory=lambda: library,
    )

    assert actions.execute("move_reachy_head", {"direction": "left"})["ok"] is True
    assert actions.execute("express_reachy_emotion", {"emotion": "happy"})["move"] == "laughing2"
    assert actions.execute("dance_reachy", {"style": "short"})["move"] == "dance1"

    assert robot.targets[0]["body_yaw"] is None
    assert library.requested == ["laughing2", "dance1"]
    assert robot.head_samples
    assert robot.body_samples
    assert robot.antenna_samples
    assert robot.cancellations == 0


def test_unknown_or_unapproved_robot_action_is_rejected() -> None:
    actions = ReachyRobotActions(FakeRobot(), threading.Event(), library_factory=FakeLibrary)

    assert actions.execute("dance_reachy", {"style": "unknown"})["ok"] is False
    assert actions.execute("shell", {"command": "rm -rf /"})["ok"] is False


def test_worker_completes_tool_with_actual_execution_result(monkeypatch) -> None:
    monkeypatch.setattr("reachy_mini_hermes.robot_tools.create_head_pose", lambda **kwargs: kwargs)
    robot = FakeRobot()
    completed = threading.Event()
    results: list[dict[str, object]] = []
    actions = ReachyRobotActions(robot, threading.Event(), library_factory=FakeLibrary)
    actions.start()

    def on_complete(result: dict[str, object]) -> None:
        results.append(result)
        completed.set()

    accepted = actions.enqueue(
        "move_reachy_head",
        {"direction": "left"},
        on_complete=on_complete,
    )

    assert accepted == {"accepted": True, "queued": "move_reachy_head"}
    assert completed.wait(2)
    assert results == [{"ok": True, "action": "move_reachy_head", "direction": "left"}]
    actions.close()


def test_manual_actions_reject_surprising_queueing_when_robot_is_busy() -> None:
    actions = ReachyRobotActions(FakeRobot(), threading.Event(), library_factory=FakeLibrary)

    first = actions.enqueue("dance_reachy", {"style": "short"}, reject_if_busy=True)
    second = actions.enqueue("move_reachy_head", {"direction": "left"}, reject_if_busy=True)

    assert first["accepted"] is True
    assert second == {"ok": False, "error": "Robot is busy", "code": "robot_busy"}
    assert actions.pending_count == 1
    assert actions.cancel() is False
    assert actions.pending_count == 0


def test_manual_stop_uses_cooperative_move_flag_without_stopping_media() -> None:
    robot = FakeRobot()
    actions = ReachyRobotActions(robot, threading.Event(), library_factory=FakeLibrary)
    actions._busy.set()

    assert actions.cancel(stop_media=False) is True
    assert actions._cancel_requested.is_set()
    assert robot.head_samples[-1] == {"current": True}
    assert robot.cancellations == 0


def test_manual_stop_interrupts_recorded_move_without_sdk_media_cancel() -> None:
    class LongMove(FakeMove):
        duration = 2.0

    class LongLibrary(FakeLibrary):
        def get(self, name: str) -> LongMove:
            self.requested.append(name)
            return LongMove()

    robot = FakeRobot()
    completed = threading.Event()
    results: list[dict[str, object]] = []
    actions = ReachyRobotActions(robot, threading.Event(), library_factory=LongLibrary)
    actions.start()

    def on_complete(result: dict[str, object]) -> None:
        results.append(result)
        completed.set()

    actions.enqueue(
        "dance_reachy",
        {"style": "energetic"},
        on_complete=on_complete,
    )
    for _ in range(100):
        if robot.head_samples:
            break
        threading.Event().wait(0.01)

    assert actions.cancel(stop_media=False) is True
    assert completed.wait(1)
    assert results == [{"ok": False, "error": "Robot action was cancelled", "action": "dance_reachy"}]
    assert robot.cancellations == 0
    assert actions.wait_idle(1)
    actions.close()


def test_manual_look_can_hold_pose_without_restoring_idle_motion(monkeypatch) -> None:
    monkeypatch.setattr("reachy_mini_hermes.robot_tools.create_head_pose", lambda **kwargs: kwargs)
    completed = threading.Event()
    lifecycle: list[str] = []
    actions = ReachyRobotActions(
        FakeRobot(),
        threading.Event(),
        before_action=lambda: lifecycle.append("before"),
        after_action=lambda: lifecycle.append("after"),
    )
    actions.start()
    actions.enqueue(
        "move_reachy_head",
        {"direction": "left"},
        hold_pose=True,
        on_complete=lambda result: completed.set(),
    )

    assert completed.wait(2)
    assert lifecycle == ["before"]
    actions.close()


def test_cancel_generation_blocks_dequeued_action_before_execution(monkeypatch) -> None:
    monkeypatch.setattr("reachy_mini_hermes.robot_tools.create_head_pose", lambda **kwargs: kwargs)
    robot = FakeRobot()
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    results: list[dict[str, object]] = []

    def before_action() -> None:
        entered.set()
        release.wait(2)

    def on_complete(result: dict[str, object]) -> None:
        results.append(result)
        completed.set()

    lifecycle: list[str] = []
    actions = ReachyRobotActions(
        robot,
        threading.Event(),
        before_action=before_action,
        after_action=lambda: lifecycle.append("after"),
    )
    actions.start()
    actions.enqueue(
        "move_reachy_head",
        {"direction": "left"},
        on_complete=on_complete,
    )
    assert entered.wait(2)
    assert actions.cancel() is True
    release.set()

    assert completed.wait(2)
    assert results[0]["ok"] is False
    assert robot.targets == []
    assert robot.cancellations == 1
    assert lifecycle == []
    actions.close()
