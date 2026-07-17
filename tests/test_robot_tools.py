from __future__ import annotations

import threading

from reachy_mini_hermes.robot_tools import ReachyRobotActions, completed_robot_tool_call


class FakeMove:
    duration = 1.0


class FakeLibrary:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def get(self, name: str) -> FakeMove:
        self.requested.append(name)
        return FakeMove()


class FakeRobot:
    def __init__(self) -> None:
        self.targets: list[dict[str, object]] = []
        self.moves: list[tuple[object, bool]] = []
        self.cancellations = 0

    def goto_target(self, **kwargs: object) -> None:
        self.targets.append(kwargs)

    def play_move(self, move: object, **kwargs: object) -> None:
        self.moves.append((move, bool(kwargs.get("sound"))))

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
    assert [sound for _, sound in robot.moves] == [False, False]


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

    actions = ReachyRobotActions(robot, threading.Event(), before_action=before_action)
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
    actions.close()
