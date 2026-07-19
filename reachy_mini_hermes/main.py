"""Reachy Mini App SDK entry point for Hermes voice conversations."""

from __future__ import annotations

import logging
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Literal

from fastapi import Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from reachy_mini import ReachyMini, ReachyMiniApp

from .agent_audit import AgentAuditLog
from .bluetooth import BluetoothGamepadService
from .config import AppConfig, default_config_path, load_config, merge_config, save_config
from .hermes_client import HermesBridgeClient
from .kids_mode import KidsProfile, hash_parent_pin, verify_parent_pin
from .robot_tools import robot_control_options
from .runtime import HermesVoiceRuntime

_LOGGER = logging.getLogger(__name__)
_STATIC_DIR = Path(__file__).resolve().parent / "static"


class SettingsUpdate(BaseModel):
    """A deliberately bounded settings payload for the app UI."""

    bridge_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    conversation_mode: str | None = None
    language: str | None = None
    stt_provider: str | None = None
    stt_model: str | None = None
    tts_provider: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None
    system_prompt: str | None = None
    continuous_conversation: bool | None = None
    conversation_timeout_seconds: float | None = Field(default=None, ge=30, le=3600)
    initial_speech_timeout_seconds: float | None = Field(default=None, ge=1, le=30)
    max_utterance_seconds: float | None = Field(default=None, ge=1, le=120)
    end_silence_seconds: float | None = Field(default=None, ge=0.1, le=5)
    vad_min_rms: float | None = Field(default=None, ge=0.001, le=0.5)
    vad_noise_multiplier: float | None = Field(default=None, ge=1, le=20)
    wake_keyword_score: float | None = Field(default=None, ge=0, le=10)
    wake_keyword_threshold: float | None = Field(default=None, ge=0.01, le=1)
    wake_cooldown_seconds: float | None = Field(default=None, ge=0.5, le=30)
    motion_enabled: bool | None = None
    barge_in_enabled: bool | None = None
    camera_enabled: bool | None = None
    camera_feed_enabled: bool | None = None
    face_tracking_enabled: bool | None = None
    face_tracking_weight: float | None = Field(default=None, ge=0, le=1)
    doa_enabled: bool | None = None
    robot_tools_enabled: bool | None = None
    realtime_model: str | None = None
    realtime_voice: str | None = None
    realtime_reasoning_effort: str | None = None


class RobotActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=32)
    value: str = Field(min_length=1, max_length=32)


class RobotNudgeRequest(BaseModel):
    axis: str = Field(min_length=1, max_length=32)
    delta: float = Field(default=0.0, ge=-10, le=10)


class BluetoothDeviceRequest(BaseModel):
    address: str = Field(pattern=r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")


class BluetoothScanRequest(BaseModel):
    seconds: int = Field(default=12, ge=5, le=30)


class GamepadEnabledRequest(BaseModel):
    enabled: bool


class PowerRequest(BaseModel):
    mode: str
    duration_minutes: float = Field(default=60, ge=1, le=480)


class ConfirmationRequest(BaseModel):
    confirm: str


class AnnouncementRequest(BaseModel):
    text: str = Field(min_length=1, max_length=15_000)
    provider: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=120)
    voice: str = Field(default="", max_length=120)
    behavior: str = Field(default="wake_and_return", max_length=32)
    repeat: int = Field(default=1, ge=1, le=10)
    pause_seconds: float = Field(default=1.0, ge=0, le=60)


class AnnouncementStopRequest(BaseModel):
    clear_queue: bool = True


class KidsModeRequest(BaseModel):
    parent_pin: str = Field(min_length=6, max_length=8, pattern=r"^[0-9]+$")
    nickname: str = Field(default="", max_length=32)
    age_band: str = Field(default="7-9", pattern=r"^(4-6|7-9|10-12)$")
    activity: str = Field(default="buddy", pattern=r"^(buddy|story|quiz|riddles|calm)$")
    language: str = Field(default="en", pattern=r"^(en|nl)$")
    duration_minutes: int = Field(default=30, ge=15, le=60)
    motion_enabled: bool = True


class ParentPinRequest(BaseModel):
    parent_pin: str = Field(min_length=6, max_length=8, pattern=r"^[0-9]+$")


class AgentProfileRequest(BaseModel):
    profile: Literal["conversation", "agent"]


class ReachyMiniHermes(ReachyMiniApp):
    """Embodied voice frontend for a user's own Hermes Agent."""

    custom_app_url: str | None = "http://0.0.0.0:8042"
    request_media_backend: str | None = "local"

    def __init__(self, running_on_wireless: bool = False) -> None:
        super().__init__(running_on_wireless=running_on_wireless)
        self._runtime: HermesVoiceRuntime | None = None
        self._bluetooth = BluetoothGamepadService(self._handle_gamepad_action)
        self._kids_pin_lock = threading.Lock()
        self._kids_pin_failures = 0
        self._kids_pin_locked_until = 0.0
        self._register_settings_routes()

    def _handle_gamepad_action(self, kind: str, action: str, value: str) -> None:
        """Route controller input through the same safety gates as the Robot tab."""
        if self._runtime is None:
            raise RuntimeError("Voice runtime has not started")
        if kind == "stop":
            self._runtime.stop_manual_robot_action()
            return
        if kind == "precision":
            self._runtime.queue_precision_robot_action(action, float(value))
            return
        self._runtime.queue_manual_robot_action(action, value)

    def _require_kids_pin_attempt_allowed(self) -> None:
        with self._kids_pin_lock:
            remaining = int(self._kids_pin_locked_until - time.monotonic())
        if remaining > 0:
            raise HTTPException(
                status_code=429,
                detail=f"Too many incorrect parent PIN attempts; try again in {remaining + 1} seconds",
                headers={"Retry-After": str(remaining + 1)},
            )

    def _record_kids_pin_result(self, *, valid: bool) -> None:
        with self._kids_pin_lock:
            if valid:
                self._kids_pin_failures = 0
                self._kids_pin_locked_until = 0.0
                return
            self._kids_pin_failures += 1
            if self._kids_pin_failures >= 5:
                self._kids_pin_locked_until = time.monotonic() + 300.0
                self._kids_pin_failures = 0

    def _register_settings_routes(self) -> None:
        if self.settings_app is None:
            return

        @self.settings_app.middleware("http")
        async def lock_management_routes(request: Request, call_next):  # type: ignore[no-untyped-def]
            """Fail closed on management APIs while the child-facing UI is locked."""
            allowed = {
                "/api/status",
                "/api/kids/stop",
                "/api/kids/parent/unlock",
                "/api/robot/stop",
                "/api/agent/stop",
            }
            if (
                request.url.path.startswith("/api/")
                and request.url.path not in allowed
                and self._runtime is not None
                and self._runtime.kids_controls_locked
            ):
                return JSONResponse(
                    status_code=423,
                    content={"detail": "Parent controls are locked while Kids Mode is active"},
                )
            return await call_next(request)

        @self.settings_app.get("/manifest.webmanifest", include_in_schema=False)
        def web_manifest() -> FileResponse:
            return FileResponse(
                _STATIC_DIR / "manifest.webmanifest",
                media_type="application/manifest+json",
                headers={"Cache-Control": "no-cache"},
            )

        @self.settings_app.get("/service-worker.js", include_in_schema=False)
        def service_worker() -> FileResponse:
            return FileResponse(
                _STATIC_DIR / "service-worker.js",
                media_type="application/javascript",
                headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
            )

        @self.settings_app.get("/api/status")
        def status() -> dict[str, object]:
            runtime_payload = self._runtime.status() if self._runtime is not None else {"state": "not_started"}
            kids_payload = runtime_payload.get("kids_mode")
            child_locked = isinstance(kids_payload, dict) and kids_payload.get("locked") is True
            try:
                config = load_config()
                config_payload: dict[str, object] = (
                    config.child_status_dict() if child_locked else config.redacted_dict()
                )
                config_error = ""
            except Exception as exc:
                config_payload = {}
                config_error = "Configuration is unavailable" if child_locked else str(exc)
            return {
                "app": "reachy_mini_hermes",
                "wake_phrase": "Hey Hermes",
                "wake_phrases": ["Hey Hermes", "Okay Nabu", "Hey Reachy"],
                "config": config_payload,
                "config_error": config_error,
                "runtime": runtime_payload,
            }

        @self.settings_app.post("/api/settings")
        def update_settings(update: SettingsUpdate) -> dict[str, object]:
            try:
                current = load_config()
                changes = update.model_dump(exclude_none=True)
                merged = merge_config(current, changes)
                path = save_config(merged)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            _LOGGER.info("Reachy Hermes settings updated at %s (secret values redacted)", path)
            return {
                "ok": True,
                "config": merged.redacted_dict(),
                "note": (
                    "Connection and conversation settings apply on the next wake. "
                    "Wake-model tuning applies after an app restart."
                ),
            }

        @self.settings_app.post("/api/agent/profile")
        def set_agent_profile(
            update: AgentProfileRequest,
            x_reachy_adult_ui: str = Header(default=""),
        ) -> dict[str, object]:
            if x_reachy_adult_ui != "unlocked":
                raise HTTPException(status_code=403, detail="An unlocked adult UI action is required")
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            try:
                agent = self._runtime.set_capability_profile(update.profile, adult_ui_unlocked=True)
                current = load_config()
                save_config(merge_config(current, {"capability_profile": update.profile}))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return {"ok": True, "agent": agent}

        @self.settings_app.post("/api/agent/stop")
        def stop_agent() -> dict[str, object]:
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            return {"ok": True, "agent": self._runtime.cancel_agent_work("stopped")}

        @self.settings_app.post("/api/test-connection")
        def test_connection(update: SettingsUpdate | None = None) -> dict[str, object]:
            try:
                config: AppConfig = load_config()
                if update is not None:
                    config = merge_config(config, update.model_dump(exclude_none=True))
                client = HermesBridgeClient(config)
                try:
                    health = client.health()
                finally:
                    client.close()
                return {"ok": True, "health": health}
            except Exception as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        @self.settings_app.get("/api/models")
        def models() -> dict[str, object]:
            try:
                client = HermesBridgeClient(load_config())
                try:
                    return {"models": client.models(), "health": client.health()}
                finally:
                    client.close()
            except Exception as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        @self.settings_app.get("/api/voice-options")
        def voice_options() -> dict[str, object]:
            try:
                client = HermesBridgeClient(load_config())
                try:
                    return client.voice_options()
                finally:
                    client.close()
            except Exception as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        @self.settings_app.post("/api/camera/test")
        def test_camera(request: ConfirmationRequest) -> dict[str, object]:
            if request.confirm.strip().lower() != "camera":
                raise HTTPException(status_code=400, detail="Confirmation must be 'camera'")
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            try:
                return {"ok": True, **self._runtime.test_camera()}
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

        @self.settings_app.post("/api/camera/snapshot")
        def camera_snapshot(
            request: ConfirmationRequest,
            authorization: str = Header(default=""),
        ) -> Response:
            expected = f"Bearer {load_config().api_key}"
            if not secrets.compare_digest(authorization, expected):
                raise HTTPException(status_code=401, detail="Unauthorized")
            if request.confirm.strip().lower() != "camera":
                raise HTTPException(status_code=400, detail="Confirmation must be 'camera'")
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            try:
                jpeg = self._runtime.camera_snapshot()
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return Response(
                content=jpeg,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store", "Content-Disposition": "inline"},
            )

        @self.settings_app.post("/api/announcements")
        def create_announcement(request: AnnouncementRequest) -> dict[str, object]:
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            try:
                return self._runtime.queue_announcement(
                    request.text,
                    provider=request.provider,
                    model=request.model,
                    voice=request.voice,
                    behavior=request.behavior,
                    repeat=request.repeat,
                    pause_seconds=request.pause_seconds,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @self.settings_app.post("/api/announcements/stop")
        def stop_announcements(request: AnnouncementStopRequest) -> dict[str, object]:
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            return self._runtime.stop_announcements(clear_queue=request.clear_queue)

        @self.settings_app.post("/api/kids/parent/setup")
        def setup_kids_parent_pin(request: ParentPinRequest) -> dict[str, object]:
            current = load_config()
            if current.kids_parent_pin_hash:
                raise HTTPException(status_code=409, detail="A Kids Mode parent PIN is already configured")
            try:
                pin_hash = hash_parent_pin(request.parent_pin)
                save_config(merge_config(current, {"kids_parent_pin_hash": pin_hash}))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "kids_parent_pin_configured": True}

        @self.settings_app.post("/api/kids/parent/unlock")
        def unlock_kids_parent_controls(request: ParentPinRequest) -> dict[str, object]:
            self._require_kids_pin_attempt_allowed()
            current = load_config()
            valid = bool(current.kids_parent_pin_hash) and verify_parent_pin(
                request.parent_pin,
                current.kids_parent_pin_hash,
            )
            self._record_kids_pin_result(valid=valid)
            if not valid:
                raise HTTPException(status_code=401, detail="Incorrect parent PIN")
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            if self._runtime.status()["kids_mode"].get("active"):  # type: ignore[union-attr]
                self._runtime.stop_kids_mode(reason="parent_unlock", fold=True)
            return {"ok": True, "kids_mode": self._runtime.unlock_kids_controls()}

        @self.settings_app.post("/api/kids/start")
        def start_kids_mode(request: KidsModeRequest) -> dict[str, object]:
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            config = load_config()
            if not config.configured:
                raise HTTPException(status_code=409, detail="Configure the Hermes bridge first")
            if not config.kids_parent_pin_hash:
                raise HTTPException(status_code=409, detail="Set a Kids Mode parent PIN first")
            self._require_kids_pin_attempt_allowed()
            valid = verify_parent_pin(request.parent_pin, config.kids_parent_pin_hash)
            self._record_kids_pin_result(valid=valid)
            if not valid:
                raise HTTPException(status_code=401, detail="Incorrect parent PIN")
            client = HermesBridgeClient(config)
            try:
                try:
                    health = client.health()
                except Exception as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
            finally:
                client.close()
            if health.get("kids_chat_available") is not True:
                raise HTTPException(
                    status_code=409,
                    detail="Kids Mode requires the private moderated child bridge route",
                )
            if health.get("kids_tts_streaming_available") is not True:
                raise HTTPException(
                    status_code=409,
                    detail="Kids Mode requires ElevenLabs Flash streaming on the private bridge",
                )
            try:
                profile = KidsProfile(**request.model_dump(exclude={"parent_pin"}))
                kids_mode = self._runtime.start_kids_mode(profile)
                save_config(merge_config(config, {"capability_profile": "conversation"}))
                return {"ok": True, "kids_mode": kids_mode}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @self.settings_app.post("/api/kids/stop")
        def stop_kids_mode() -> dict[str, object]:
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            try:
                return {
                    "ok": True,
                    "kids_mode": self._runtime.stop_kids_mode(reason="parent", fold=True),
                }
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @self.settings_app.get("/api/bluetooth/status")
        def bluetooth_status() -> dict[str, object]:
            return {"ok": True, **self._bluetooth.refresh()}

        @self.settings_app.post("/api/bluetooth/scan")
        def bluetooth_scan(request: BluetoothScanRequest) -> dict[str, object]:
            try:
                return {"ok": True, **self._bluetooth.scan(seconds=request.seconds)}
            except (RuntimeError, ValueError) as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

        @self.settings_app.post("/api/bluetooth/pair")
        def bluetooth_pair(request: BluetoothDeviceRequest) -> dict[str, object]:
            try:
                return {"ok": True, **self._bluetooth.pair(request.address)}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        @self.settings_app.post("/api/bluetooth/connect")
        def bluetooth_connect(request: BluetoothDeviceRequest) -> dict[str, object]:
            try:
                return {"ok": True, **self._bluetooth.connect(request.address)}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        @self.settings_app.post("/api/bluetooth/disconnect")
        def bluetooth_disconnect(request: BluetoothDeviceRequest) -> dict[str, object]:
            try:
                return {"ok": True, **self._bluetooth.disconnect(request.address)}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        @self.settings_app.post("/api/bluetooth/remove")
        def bluetooth_remove(request: BluetoothDeviceRequest) -> dict[str, object]:
            try:
                return {"ok": True, **self._bluetooth.remove(request.address)}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        @self.settings_app.post("/api/bluetooth/gamepad")
        def bluetooth_gamepad(request: GamepadEnabledRequest) -> dict[str, object]:
            try:
                current = load_config()
                save_config(merge_config(current, {"gamepad_enabled": request.enabled}))
                return {"ok": True, **self._bluetooth.set_gamepad_enabled(request.enabled)}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        @self.settings_app.get("/api/robot/options")
        def robot_options() -> dict[str, object]:
            return {"ok": True, **robot_control_options()}

        @self.settings_app.post("/api/robot/action")
        def robot_action(request: RobotActionRequest) -> dict[str, object]:
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            try:
                return self._runtime.queue_manual_robot_action(request.action, request.value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @self.settings_app.get("/api/robot/pose")
        def robot_pose() -> dict[str, object]:
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            try:
                return {"ok": True, "pose": self._runtime.robot_pose()}
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

        @self.settings_app.post("/api/robot/nudge")
        def robot_nudge(request: RobotNudgeRequest) -> dict[str, object]:
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            try:
                return self._runtime.queue_precision_robot_action(request.axis, request.delta)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @self.settings_app.post("/api/robot/stop")
        def stop_robot_action() -> dict[str, object]:
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            try:
                return self._runtime.stop_manual_robot_action()
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @self.settings_app.post("/api/power")
        def power(request: PowerRequest) -> dict[str, object]:
            if self._runtime is None:
                raise HTTPException(status_code=409, detail="Voice runtime has not started")
            try:
                runtime = self._runtime.set_power_mode(
                    request.mode,
                    duration_seconds=request.duration_minutes * 60.0,
                )
                return {"ok": True, "runtime": runtime}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @self.settings_app.post("/api/app-off")
        def app_off(request: ConfirmationRequest) -> dict[str, object]:
            if request.confirm.strip().lower() != "off":
                raise HTTPException(status_code=400, detail="Confirmation must be 'off'")

            def stop_app() -> None:
                try:
                    # The daemon intentionally holds this response until the app
                    # has exited. Waiting for it from inside the app creates a
                    # shutdown cycle, so dispatch the request and close without
                    # waiting for response headers.
                    with socket.create_connection(("127.0.0.1", 8000), timeout=2.0) as connection:
                        connection.sendall(
                            b"POST /api/apps/stop-current-app HTTP/1.1\r\n"
                            b"Host: 127.0.0.1\r\n"
                            b"Content-Length: 0\r\n"
                            b"Connection: close\r\n\r\n"
                        )
                except Exception:
                    _LOGGER.exception("Could not stop Reachy app")

            timer = threading.Timer(0.4, stop_app)
            timer.daemon = True
            timer.start()
            return {"ok": True, "state": "stopping"}

        @self.settings_app.post("/api/shutdown")
        def shutdown(request: ConfirmationRequest) -> dict[str, object]:
            if request.confirm.strip().lower() != "shutdown":
                raise HTTPException(status_code=400, detail="Confirmation must be 'shutdown'")
            if self._runtime is not None:
                try:
                    self._runtime.set_power_mode("sleep")
                except RuntimeError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc

            def poweroff() -> None:
                try:
                    subprocess.run(
                        ["sudo", "-n", "systemctl", "poweroff", "--no-wall"],
                        check=True,
                        timeout=10,
                    )
                except Exception:
                    _LOGGER.exception("Could not shut down Reachy Pi")

            threading.Timer(0.8, poweroff).start()
            return {"ok": True, "state": "shutting_down"}

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """Run wake detection, gamepad monitoring, and serialized Hermes voice turns."""
        current = load_config()
        if current.capability_profile != "conversation":
            save_config(merge_config(current, {"capability_profile": "conversation"}))
        audit = AgentAuditLog(default_config_path().with_name("agent-audit.jsonl"))
        self._runtime = HermesVoiceRuntime(reachy_mini, stop_event, agent_audit=audit)
        try:
            if load_config().gamepad_enabled:
                self._bluetooth.set_gamepad_enabled(True)
            self._runtime.run()
        finally:
            self._bluetooth.close()


def run_cli() -> None:
    """Launch the app outside the daemon for development."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = ReachyMiniHermes()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()


if __name__ == "__main__":
    run_cli()
