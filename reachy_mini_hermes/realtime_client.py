"""Synchronous Reachy client for the authenticated Realtime bridge."""

from __future__ import annotations

import base64
import json
import queue
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

import numpy as np
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection, connect

from .config import AppConfig


class RealtimeBridgeError(RuntimeError):
    """Raised when the Realtime bridge cannot establish or maintain a session."""


@dataclass(slots=True)
class RealtimeEvent:
    type: str
    payload: dict[str, Any]


def realtime_url(bridge_url: str) -> str:
    parsed = urlparse(bridge_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "/v1/realtime", "", "", ""))


class RealtimeBridgeSession:
    """Keep WebSocket receive work off the robot's audio loop."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._socket: ClientConnection | None = None
        self._events: queue.Queue[RealtimeEvent] = queue.Queue(maxsize=512)
        self._receiver: threading.Thread | None = None
        self._closed = threading.Event()

    def start(self) -> None:
        try:
            self._socket = connect(
                realtime_url(self.config.bridge_url),
                additional_headers={"Authorization": f"Bearer {self.config.api_key}"},
                open_timeout=10,
                close_timeout=3,
                ping_interval=20,
                max_size=2 * 1024 * 1024,
            )
            self._socket.send(
                json.dumps(
                    {
                        "type": "session.start",
                        "model": self.config.realtime_model,
                        "voice": self.config.realtime_voice,
                        "reasoning_effort": self.config.realtime_reasoning_effort,
                        "camera_enabled": self.config.camera_enabled,
                        "robot_tools_enabled": self.config.robot_tools_enabled,
                        "agent_tools_enabled": self.config.agent_tools_enabled,
                        "power_tools_enabled": self.config.power_tools_enabled,
                        "agent_model": self.config.model,
                        "session_id": f"reachy-realtime-{self.config.instance_id}",
                        "system_prompt": self.config.system_prompt,
                    }
                )
            )
        except Exception as exc:
            self.close()
            raise RealtimeBridgeError(f"Could not open Realtime session: {exc}") from exc
        self._receiver = threading.Thread(target=self._receive_loop, name="reachy-realtime-events", daemon=True)
        self._receiver.start()

    def _receive_loop(self) -> None:
        assert self._socket is not None
        try:
            for message in self._socket:
                if not isinstance(message, str):
                    continue
                payload = json.loads(message)
                event = RealtimeEvent(str(payload.get("type") or "unknown"), payload)
                try:
                    self._events.put(event, timeout=0.5)
                except queue.Full:
                    self._events.get_nowait()
                    self._events.put_nowait(event)
        except ConnectionClosed as exc:
            if not self._closed.is_set():
                self._events.put_nowait(
                    RealtimeEvent("bridge.error", {"type": "bridge.error", "error": str(exc)})
                )
        except Exception as exc:
            if not self._closed.is_set():
                self._events.put_nowait(
                    RealtimeEvent("bridge.error", {"type": "bridge.error", "error": str(exc)})
                )

    def send_audio(self, samples_24k: np.ndarray) -> None:
        if self._socket is None:
            raise RealtimeBridgeError("Realtime session is not connected")
        clipped = np.clip(samples_24k, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype("<i2", copy=False).tobytes()
        encoded = base64.b64encode(pcm).decode("ascii")
        try:
            self._socket.send(json.dumps({"type": "input_audio_buffer.append", "audio": encoded}))
        except Exception as exc:
            raise RealtimeBridgeError(f"Could not stream microphone audio: {exc}") from exc

    def events(self) -> list[RealtimeEvent]:
        result: list[RealtimeEvent] = []
        while True:
            try:
                result.append(self._events.get_nowait())
            except queue.Empty:
                return result

    @staticmethod
    def audio_samples(event: RealtimeEvent) -> np.ndarray:
        encoded = str(event.payload.get("delta") or "")
        if not encoded:
            return np.empty(0, dtype=np.float32)
        pcm = np.frombuffer(base64.b64decode(encoded), dtype="<i2")
        return pcm.astype(np.float32) / 32768.0

    def clear_output(self) -> None:
        if self._socket is not None:
            self._socket.send(json.dumps({"type": "output_audio_buffer.clear"}))

    def truncate_audio(self, item_id: str, audio_end_ms: int) -> None:
        """Tell a WebSocket Realtime session how much audio was actually played."""
        if self._socket is None or not item_id:
            return
        try:
            self._socket.send(
                json.dumps(
                    {
                        "type": "conversation.item.truncate",
                        "item_id": item_id,
                        "content_index": 0,
                        "audio_end_ms": max(0, audio_end_ms),
                    }
                )
            )
        except Exception as exc:
            raise RealtimeBridgeError(f"Could not truncate interrupted audio: {exc}") from exc

    def send_camera_frame(self, call_id: str, jpeg: bytes) -> None:
        """Attach one on-demand camera frame and complete its tool call."""
        if self._socket is None:
            raise RealtimeBridgeError("Realtime session is not connected")
        if not call_id:
            raise RealtimeBridgeError("Camera tool call did not include a call ID")
        if not jpeg or len(jpeg) > 1_000_000:
            raise RealtimeBridgeError("Camera JPEG must be between 1 byte and 1 MB")
        image_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        events = [
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": '{"ok":true,"image_attached":true,"capture_count":1}',
                },
            },
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "This is the single current Reachy camera frame requested by the user. "
                                "Use it to answer the latest visual question."
                            ),
                        },
                        {"type": "input_image", "image_url": image_url, "detail": "high"},
                    ],
                },
            },
            {"type": "response.create"},
        ]
        try:
            for event in events:
                self._socket.send(json.dumps(event))
        except Exception as exc:
            raise RealtimeBridgeError(f"Could not send camera frame: {exc}") from exc

    def send_camera_error(self, call_id: str, message: str) -> None:
        """Complete a failed camera tool call so the model can explain it."""
        if self._socket is None or not call_id:
            return
        try:
            self._socket.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": f"Camera capture failed: {message}",
                        },
                    }
                )
            )
            self._socket.send(json.dumps({"type": "response.create"}))
        except Exception as exc:
            raise RealtimeBridgeError(f"Could not report camera failure: {exc}") from exc

    def send_tool_result(
        self,
        call_id: str,
        result: dict[str, object],
        *,
        continue_response: bool = True,
    ) -> None:
        """Complete one robot-local function call and optionally continue the response."""
        if self._socket is None:
            raise RealtimeBridgeError("Realtime session is not connected")
        if not call_id:
            raise RealtimeBridgeError("Robot tool call did not include a call ID")
        try:
            self._socket.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(result),
                        },
                    }
                )
            )
            if continue_response:
                self._socket.send(json.dumps({"type": "response.create"}))
        except Exception as exc:
            raise RealtimeBridgeError(f"Could not send robot tool result: {exc}") from exc

    def close(self) -> None:
        self._closed.set()
        socket, self._socket = self._socket, None
        if socket is not None:
            try:
                socket.send(json.dumps({"type": "session.stop"}))
            except Exception:
                pass
            try:
                socket.close()
            except Exception:
                pass
        if self._receiver is not None and self._receiver is not threading.current_thread():
            self._receiver.join(timeout=1.0)
