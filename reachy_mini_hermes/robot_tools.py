"""Curated local embodiment tools for Reachy Mini Realtime conversations."""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

_LOGGER = logging.getLogger(__name__)

ROBOT_TOOL_NAMES = frozenset(
    {
        "move_reachy_head",
        "express_reachy_emotion",
        "dance_reachy",
    }
)

_LOOK_POSES: dict[str, dict[str, float]] = {
    "left": {"yaw": 35.0},
    "right": {"yaw": -35.0},
    "up": {"pitch": -25.0},
    "down": {"pitch": 25.0},
    "center": {},
}

_EMOTION_MOVES: dict[str, str] = {
    "happy": "laughing2",
    "excited": "success2",
    "loving": "loving1",
    "grateful": "grateful1",
    "thinking": "thoughtful1",
    "confused": "confused1",
    "sad": "sad2",
    "surprised": "surprised1",
    "calm": "calming1",
    "welcoming": "welcoming2",
    "yes": "yes1",
    "no": "no1",
}

_DANCE_MOVES: dict[str, str] = {
    "short": "dance1",
    "groovy": "dance2",
    "energetic": "dance3",
}

LOOK_DIRECTIONS = tuple(_LOOK_POSES)
EMOTIONS = tuple(_EMOTION_MOVES)
DANCE_STYLES = tuple(_DANCE_MOVES)


def manual_robot_action(action: str, value: str) -> tuple[str, dict[str, object]]:
    """Translate one UI control into an allow-listed semantic robot tool call."""
    action = action.strip().lower()
    value = value.strip().lower()
    if action == "look" and value in _LOOK_POSES:
        return "move_reachy_head", {"direction": value}
    if action == "emotion" and value in _EMOTION_MOVES:
        return "express_reachy_emotion", {"emotion": value}
    if action == "dance" and value in _DANCE_MOVES:
        return "dance_reachy", {"style": value}
    raise ValueError("Unknown or unsupported manual robot action")


def robot_control_options() -> dict[str, list[str]]:
    """Return the UI-safe semantic controls without exposing raw motors or joints."""
    return {
        "look": list(LOOK_DIRECTIONS),
        "emotion": list(EMOTIONS),
        "dance": list(DANCE_STYLES),
    }


class RobotLike(Protocol):
    def goto_target(self, **kwargs: object) -> object: ...

    def play_move(self, move: object, **kwargs: object) -> object: ...

    def cancel_move(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RobotToolCall:
    call_id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class QueuedRobotAction:
    name: str
    arguments: dict[str, object]
    generation: int
    on_complete: Callable[[dict[str, object]], None] | None = None
    hold_pose: bool = False


def create_head_pose(**kwargs: float) -> object:
    """Import the SDK pose helper lazily so unit tests remain hardware-independent."""
    from reachy_mini.utils import create_head_pose as sdk_create_head_pose

    return sdk_create_head_pose(**kwargs)


def _default_library_factory() -> object:
    from reachy_mini.motion.recorded_move import RecordedMoves

    return RecordedMoves("pollen-robotics/reachy-mini-emotions-library")


def completed_robot_tool_call(kind: str, payload: dict[str, object]) -> RobotToolCall | None:
    """Parse a completed, allow-listed robot-local Realtime function call."""
    if kind != "response.output_item.done":
        return None
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "")
    call_id = str(item.get("call_id") or "")
    if (
        item.get("type") != "function_call"
        or item.get("status") != "completed"
        or name not in ROBOT_TOOL_NAMES
        or not call_id
    ):
        return None
    raw_arguments = item.get("arguments") or "{}"
    try:
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    except json.JSONDecodeError:
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return RobotToolCall(call_id, name, arguments)


class ReachyRobotActions:
    """Serialize explicit robot actions away from the microphone streaming loop."""

    def __init__(
        self,
        robot: RobotLike,
        stop_event: threading.Event,
        *,
        library_factory: Callable[[], object] = _default_library_factory,
        before_action: Callable[[], None] | None = None,
        after_action: Callable[[], None] | None = None,
        on_result: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self.robot = robot
        self.stop_event = stop_event
        self._library_factory = library_factory
        self._before_action = before_action
        self._after_action = after_action
        self._on_result = on_result
        self._library: object | None = None
        self._queue: queue.Queue[QueuedRobotAction | None] = queue.Queue(maxsize=4)
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._busy = threading.Event()
        self._state_lock = threading.Lock()
        self._generation = 0

    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="reachy-hermes-actions", daemon=True)
        self._thread.start()

    def enqueue(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        on_complete: Callable[[dict[str, object]], None] | None = None,
        hold_pose: bool = False,
    ) -> dict[str, object]:
        if name not in ROBOT_TOOL_NAMES:
            return {"ok": False, "error": "Robot action is not allow-listed"}
        if self._closed.is_set() or self.stop_event.is_set():
            return {"ok": False, "error": "Robot action controller is stopping"}
        with self._state_lock:
            generation = self._generation
        try:
            self._queue.put_nowait(
                QueuedRobotAction(name, dict(arguments), generation, on_complete, hold_pose)
            )
        except queue.Full:
            return {"ok": False, "error": "Robot action queue is full"}
        return {"accepted": True, "queued": name}

    def _generation_matches(self, generation: int) -> bool:
        with self._state_lock:
            return generation == self._generation and not self._closed.is_set()

    def execute(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        """Execute one action synchronously; the worker calls this off the audio loop."""
        if name == "move_reachy_head":
            direction = str(arguments.get("direction") or "").lower()
            pose = _LOOK_POSES.get(direction)
            if pose is None:
                return {"ok": False, "error": "Unknown look direction"}
            target = create_head_pose(**pose, degrees=True)
            self.robot.goto_target(head=target, antennas=None, body_yaw=None, duration=0.6)
            return {"ok": True, "action": name, "direction": direction}

        if name == "express_reachy_emotion":
            emotion = str(arguments.get("emotion") or "").lower()
            move_name = _EMOTION_MOVES.get(emotion)
            if move_name is None:
                return {"ok": False, "error": "Unknown emotion"}
            self._play_recorded_move(move_name)
            return {"ok": True, "action": name, "emotion": emotion, "move": move_name}

        if name == "dance_reachy":
            style = str(arguments.get("style") or "short").lower()
            move_name = _DANCE_MOVES.get(style)
            if move_name is None:
                return {"ok": False, "error": "Unknown dance style"}
            self._play_recorded_move(move_name)
            return {"ok": True, "action": name, "style": style, "move": move_name}

        return {"ok": False, "error": "Robot action is not allow-listed"}

    def _play_recorded_move(self, move_name: str) -> None:
        if self._library is None:
            self._library = self._library_factory()
        get_move = getattr(self._library, "get", None)
        if not callable(get_move):
            raise RuntimeError("Reachy recorded-move library is unavailable")
        move = get_move(move_name)
        # Recorded emotion audio would compete with Hermes speech. Keep the
        # authentic Reachy motion while Realtime remains the sole voice source.
        self.robot.play_move(
            move,
            play_frequency=50.0,
            initial_goto_duration=0.25,
            sound=False,
        )

    def _run(self) -> None:
        while not self._closed.is_set() and not self.stop_event.is_set():
            try:
                action = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if action is None:
                self._queue.task_done()
                break
            if not self._generation_matches(action.generation):
                result = {"ok": False, "error": "Robot action was cancelled", "action": action.name}
                self._queue.task_done()
                if action.on_complete is not None:
                    action.on_complete(result)
                if self._on_result is not None:
                    self._on_result(action.name, result)
                continue
            self._busy.set()
            result: dict[str, object]
            try:
                if self._before_action is not None:
                    self._before_action()
                if not self._generation_matches(action.generation):
                    result = {"ok": False, "error": "Robot action was cancelled", "action": action.name}
                else:
                    result = self.execute(action.name, action.arguments)
                    if not self._generation_matches(action.generation):
                        result = {"ok": False, "error": "Robot action was cancelled", "action": action.name}
                if not result.get("ok"):
                    _LOGGER.warning("Reachy action rejected: %s", result.get("error"))
            except Exception as exc:
                _LOGGER.exception("Reachy action failed: %s", action.name)
                result = {"ok": False, "error": str(exc), "action": action.name}
            finally:
                try:
                    if self._after_action is not None and not action.hold_pose:
                        self._after_action()
                finally:
                    self._busy.clear()
                    self._queue.task_done()
            if self._on_result is not None:
                self._on_result(action.name, result)
            if action.on_complete is not None:
                action.on_complete(result)

    def cancel(self) -> bool:
        """Stop the active move and discard queued actions for privacy/power transitions."""
        with self._state_lock:
            self._generation += 1
        cancelled_active = self._busy.is_set()
        if cancelled_active:
            try:
                self.robot.cancel_move()
            except Exception:
                _LOGGER.debug("No active Reachy move to cancel", exc_info=True)
        while True:
            try:
                pending = self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._queue.task_done()
                if pending is None:
                    break
                result = {"ok": False, "error": "Robot action was cancelled", "action": pending.name}
                if pending.on_complete is not None:
                    pending.on_complete(result)
                if self._on_result is not None:
                    self._on_result(pending.name, result)
        return cancelled_active

    def close(self) -> None:
        self.cancel()
        self._closed.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
