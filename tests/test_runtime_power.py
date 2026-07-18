from __future__ import annotations

import threading

import pytest

from reachy_mini_hermes.config import AppConfig
from reachy_mini_hermes.hermes_client import SpeechAudio
from reachy_mini_hermes.runtime import (
    HermesVoiceRuntime,
    PowerModeToolCall,
    RealtimePlayback,
    completed_camera_call_id,
    completed_power_mode_call,
    doa_yaw_degrees,
    realtime_audio_item_id,
)


class FakeMedia:
    def __init__(self) -> None:
        self.recording = False
        self.starts = 0
        self.stops = 0
        self.playing_starts = 0
        self.played: list[str] = []
        self.doa: tuple[float, bool] = (0.0, False)

    def start_playing(self) -> None:
        self.playing_starts += 1

    def play_sound(self, path: str) -> None:
        self.played.append(path)

    def get_DoA(self) -> tuple[float, bool]:
        return self.doa

    def start_recording(self) -> None:
        self.recording = True
        self.starts += 1

    def stop_recording(self) -> None:
        self.recording = False
        self.stops += 1

    def get_frame_jpeg(self) -> bytes:
        return b"test-jpeg"


class FakeRobot:
    def __init__(self) -> None:
        self.media = FakeMedia()
        self.tracking_started: list[float] = []
        self.tracking_stops = 0
        self.sleep_calls = 0

    def goto_sleep(self) -> None:
        self.sleep_calls += 1

    def start_head_tracking(self, weight: float = 1.0) -> None:
        self.tracking_started.append(weight)

    def stop_head_tracking(self) -> None:
        self.tracking_stops += 1


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
    assert robot.sleep_calls == 1
    assert motor_modes[-2:] == [True, False]

    standby = runtime.set_power_mode("standby")
    assert standby["power_mode"] == "standby"
    assert robot.media.recording is True
    assert motor_modes[-1] is False


def test_standby_folds_head_before_releasing_motor_torque() -> None:
    robot = FakeRobot()
    runtime = HermesVoiceRuntime(robot, threading.Event())
    motor_modes: list[bool] = []
    runtime._set_motor_mode = lambda enabled, wake=False: motor_modes.append(enabled)  # type: ignore[method-assign]

    status = runtime.set_power_mode("standby")

    assert status["power_mode"] == "standby"
    assert robot.sleep_calls == 1
    assert motor_modes == [True, False]
    assert runtime._head_safely_folded is True


def test_already_folded_standby_does_not_replay_sleep_motion() -> None:
    robot = FakeRobot()
    runtime = HermesVoiceRuntime(robot, threading.Event())
    runtime._head_safely_folded = True
    motor_modes: list[bool] = []
    runtime._set_motor_mode = lambda enabled, wake=False: motor_modes.append(enabled)  # type: ignore[method-assign]

    runtime.set_power_mode("standby")

    assert robot.sleep_calls == 0
    assert motor_modes == [False]


def test_sleep_motion_failure_keeps_torque_enabled_instead_of_dropping_head() -> None:
    class FailingSleepRobot(FakeRobot):
        def goto_sleep(self) -> None:
            raise RuntimeError("movement blocked")

    runtime = HermesVoiceRuntime(FailingSleepRobot(), threading.Event())
    motor_modes: list[bool] = []
    runtime._set_motor_mode = lambda enabled, wake=False: motor_modes.append(enabled)  # type: ignore[method-assign]

    status = runtime.set_power_mode("sleep")

    assert motor_modes == [True, True]
    assert "motors remain enabled" in str(status["last_error"])
    assert status["power_mode"] == "sleep"


def test_meeting_mode_has_bounded_timer() -> None:
    robot = FakeRobot()
    runtime = HermesVoiceRuntime(robot, threading.Event())
    runtime._set_motor_mode = lambda enabled, wake=False: None  # type: ignore[method-assign]
    status = runtime.set_power_mode("meeting", duration_seconds=1)
    assert status["power_mode"] == "meeting"
    remaining = status["meeting_seconds_remaining"]
    assert isinstance(remaining, int)
    assert 59 <= remaining <= 60


def test_awake_runs_physical_wake_motion() -> None:
    runtime = HermesVoiceRuntime(FakeRobot(), threading.Event())
    calls: list[tuple[bool, bool]] = []
    runtime._set_motor_mode = (  # type: ignore[method-assign]
        lambda enabled, wake=False: calls.append((enabled, wake))
    )

    status = runtime.set_power_mode("awake")

    assert status["power_mode"] == "awake"
    assert calls[-1] == (True, True)


def test_realtime_playback_remains_audible_after_generation_finishes() -> None:
    playback = RealtimePlayback(item_id="item-123")
    playback.add(now=10.0, duration_seconds=2.0)
    playback.add(now=10.1, duration_seconds=2.0)

    assert playback.audible(13.0)
    assert playback.played_ms(11.5) == 1500
    assert not playback.audible(14.1)

    playback.reset()
    assert playback.item_id == ""
    assert playback.played_ms(15.0) == 0


def test_realtime_audio_item_tracking_ignores_function_calls() -> None:
    function_call = {
        "item": {
            "id": "item-function",
            "type": "function_call",
            "name": "express_reachy_emotion",
        }
    }
    assistant_message = {
        "item": {
            "id": "item-message",
            "type": "message",
            "role": "assistant",
        }
    }

    assert realtime_audio_item_id("response.output_item.added", function_call) == ""
    assert realtime_audio_item_id("response.output_item.added", assistant_message) == "item-message"
    assert (
        realtime_audio_item_id(
            "response.output_audio.delta",
            {"item_id": "item-audio", "delta": "ignored"},
        )
        == "item-audio"
    )


def test_power_mode_tool_requires_completed_output_item() -> None:
    completed: dict[str, object] = {
        "item": {
            "type": "function_call",
            "name": "set_reachy_power_mode",
            "status": "completed",
            "call_id": "call-power",
            "arguments": '{"mode":"meeting","duration_minutes":45}',
        }
    }
    incomplete: dict[str, object] = {
        "item": {**completed["item"], "status": "incomplete"},  # type: ignore[dict-item]
    }

    assert completed_power_mode_call("response.function_call_arguments.done", completed) is None
    assert completed_power_mode_call("response.output_item.done", incomplete) is None
    call = completed_power_mode_call("response.output_item.done", completed)
    assert call is not None
    assert call.call_id == "call-power"
    assert call.mode == "meeting"
    assert call.duration_minutes == 45


def test_standby_requests_current_conversation_to_stop_and_awake_clears_it() -> None:
    runtime = HermesVoiceRuntime(FakeRobot(), threading.Event())
    runtime._set_motor_mode = lambda enabled, wake=False: None  # type: ignore[method-assign]

    runtime.set_power_mode("standby")
    assert runtime._conversation_stop_requested.is_set()

    runtime.set_power_mode("awake")
    assert not runtime._conversation_stop_requested.is_set()


def test_power_mode_tool_applies_meeting_before_ending_realtime_session() -> None:
    class Session:
        def __init__(self) -> None:
            self.results: list[tuple[str, dict[str, object], bool]] = []

        def send_tool_result(
            self,
            call_id: str,
            result: dict[str, object],
            *,
            continue_response: bool = True,
        ) -> None:
            self.results.append((call_id, result, continue_response))

    runtime = HermesVoiceRuntime(FakeRobot(), threading.Event())
    runtime._set_motor_mode = lambda enabled, wake=False: None  # type: ignore[method-assign]
    session = Session()

    result = runtime._handle_power_mode_call(
        session,  # type: ignore[arg-type]
        PowerModeToolCall("call-meeting", "meeting", 45),
    )

    assert result == {"ok": True, "mode": "meeting", "duration_minutes": 45}
    assert runtime.status()["power_mode"] == "meeting"
    assert runtime._conversation_stop_requested.is_set()
    assert session.results == [("call-meeting", result, False)]


def test_manual_robot_action_auto_wakes_and_manual_stop_restores_playback() -> None:
    class Actions:
        def __init__(self) -> None:
            self.queued: list[tuple[str, dict[str, object], bool]] = []

        def enqueue(
            self,
            name: str,
            arguments: dict[str, object],
            *,
            hold_pose: bool = False,
        ) -> dict[str, object]:
            self.queued.append((name, arguments, hold_pose))
            return {"accepted": True, "queued": name}

        def cancel(self) -> bool:
            return True

    robot = FakeRobot()
    runtime = HermesVoiceRuntime(robot, threading.Event())
    runtime._set_motor_mode = lambda enabled, wake=False: None  # type: ignore[method-assign]
    actions = Actions()
    runtime._actions = actions  # type: ignore[assignment]

    result = runtime.queue_manual_robot_action("look", "left")

    assert result["ok"] is True
    assert result["power_mode"] == "awake"
    assert actions.queued == [("move_reachy_head", {"direction": "left"}, True)]
    assert runtime.status()["power_mode"] == "awake"

    stopped = runtime.stop_manual_robot_action()
    assert stopped == {"ok": True, "active_move_cancelled": True, "queue_cleared": True}
    assert robot.media.playing_starts == 1


def test_manual_robot_action_is_blocked_in_privacy_modes() -> None:
    class Actions:
        def cancel(self) -> bool:
            return False

    runtime = HermesVoiceRuntime(FakeRobot(), threading.Event())
    runtime._set_motor_mode = lambda enabled, wake=False: None  # type: ignore[method-assign]
    runtime._actions = Actions()  # type: ignore[assignment]
    runtime.set_power_mode("sleep")

    with pytest.raises(RuntimeError, match="blocked"):
        runtime.queue_manual_robot_action("dance", "short")


def test_camera_test_captures_locally_without_returning_image() -> None:
    runtime = HermesVoiceRuntime(FakeRobot(), threading.Event())

    result = runtime.test_camera()

    assert result == {"bytes": 9, "content_type": "image/jpeg"}
    assert runtime.status()["camera_captures"] == 1


def test_camera_capture_requires_completed_output_item() -> None:
    completed: dict[str, object] = {
        "item": {
            "type": "function_call",
            "name": "capture_reachy_camera",
            "status": "completed",
            "call_id": "call-camera",
        }
    }
    cancelled: dict[str, object] = {
        "item": {
            "type": "function_call",
            "name": "capture_reachy_camera",
            "status": "incomplete",
            "call_id": "call-camera",
        }
    }

    assert completed_camera_call_id("response.output_item.done", completed) == "call-camera"
    assert completed_camera_call_id("response.function_call_arguments.done", completed) == ""
    assert completed_camera_call_id("response.output_item.done", cancelled) == ""


def test_privacy_mode_stops_local_face_tracking() -> None:
    robot = FakeRobot()
    runtime = HermesVoiceRuntime(robot, threading.Event())
    runtime._set_motor_mode = lambda enabled, wake=False: None  # type: ignore[method-assign]
    runtime._set_face_tracking(True, weight=0.65)

    assert runtime.status()["face_tracking_active"] is True
    runtime.set_power_mode("meeting", duration_seconds=60)

    assert robot.tracking_stops == 1
    assert runtime.status()["face_tracking_active"] is False


def test_doa_angle_is_converted_to_reachy_yaw_with_deadband_and_clamp() -> None:
    assert doa_yaw_degrees(1.5707963267948966) == 0.0
    assert doa_yaw_degrees(0.7853981633974483) == 36.0
    assert doa_yaw_degrees(0.0) == 60.0


def test_privacy_action_cancel_restarts_playback_on_standby() -> None:
    class Actions:
        def cancel(self) -> bool:
            return True

    robot = FakeRobot()
    runtime = HermesVoiceRuntime(robot, threading.Event())
    runtime._actions = Actions()  # type: ignore[assignment]
    runtime._set_motor_mode = lambda enabled, wake=False: None  # type: ignore[method-assign]

    runtime.set_power_mode("sleep")
    runtime.set_power_mode("standby")

    assert robot.media.playing_starts == 1


def test_doa_requires_current_speech_detection() -> None:
    class Motion:
        def __init__(self) -> None:
            self.yaws: list[float] = []

        def orient_to_sound(self, yaw: float) -> None:
            self.yaws.append(yaw)

    robot = FakeRobot()
    runtime = HermesVoiceRuntime(robot, threading.Event())
    motion = Motion()
    runtime._motion = motion  # type: ignore[assignment]
    config = AppConfig(doa_enabled=True)

    robot.media.doa = (0.7853981633974483, False)
    runtime._orient_to_voice(config)
    assert motion.yaws == []

    robot.media.doa = (0.7853981633974483, True)
    runtime._orient_to_voice(config)
    assert motion.yaws == [36.0]


def test_pipeline_playback_does_not_start_in_privacy_mode() -> None:
    robot = FakeRobot()
    runtime = HermesVoiceRuntime(robot, threading.Event())
    runtime._set_motor_mode = lambda enabled, wake=False: None  # type: ignore[method-assign]
    runtime.set_power_mode("sleep")
    played_before = list(robot.media.played)

    result = runtime._play_response(SpeechAudio(b"audio", "audio/mpeg", ".mp3", "test"), "hello")

    assert result is False
    assert robot.media.played == played_before
