from __future__ import annotations

import threading

from reachy_mini_hermes.runtime import HermesVoiceRuntime


class FakeMedia:
    def __init__(self) -> None:
        self.recording = False
        self.starts = 0
        self.stops = 0

    def start_recording(self) -> None:
        self.recording = True
        self.starts += 1

    def stop_recording(self) -> None:
        self.recording = False
        self.stops += 1


class FakeRobot:
    def __init__(self) -> None:
        self.media = FakeMedia()


def test_sleep_stops_microphone_and_standby_restores_local_wake() -> None:
    robot = FakeRobot()
    runtime = HermesVoiceRuntime(robot, threading.Event())
    motor_modes: list[bool] = []
    runtime._set_motor_mode = lambda enabled, wake=False: motor_modes.append(enabled)  # type: ignore[method-assign]
    runtime._recording = True
    robot.media.recording = True

    sleep = runtime.set_power_mode("sleep")
    assert sleep["power_mode"] == "sleep"
    assert robot.media.recording is False
    assert motor_modes[-1] is False

    standby = runtime.set_power_mode("standby")
    assert standby["power_mode"] == "standby"
    assert robot.media.recording is True
    assert motor_modes[-1] is False


def test_meeting_mode_has_bounded_timer() -> None:
    robot = FakeRobot()
    runtime = HermesVoiceRuntime(robot, threading.Event())
    runtime._set_motor_mode = lambda enabled, wake=False: None  # type: ignore[method-assign]
    status = runtime.set_power_mode("meeting", duration_seconds=1)
    assert status["power_mode"] == "meeting"
    remaining = status["meeting_seconds_remaining"]
    assert isinstance(remaining, int)
    assert 59 <= remaining <= 60
