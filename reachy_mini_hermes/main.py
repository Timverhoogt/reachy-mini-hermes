"""Reachy Mini App SDK entry point for Hermes voice conversations."""

from __future__ import annotations

import logging
import secrets
import socket
import subprocess
import threading
from pathlib import Path

from fastapi import Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from reachy_mini import ReachyMini, ReachyMiniApp

from .config import AppConfig, load_config, merge_config, save_config
from .hermes_client import HermesBridgeClient
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


class PowerRequest(BaseModel):
    mode: str
    duration_minutes: float = Field(default=60, ge=1, le=480)


class ConfirmationRequest(BaseModel):
    confirm: str


class ReachyMiniHermes(ReachyMiniApp):
    """Embodied voice frontend for a user's own Hermes Agent."""

    custom_app_url: str | None = "http://0.0.0.0:8042"
    request_media_backend: str | None = "local"

    def __init__(self, running_on_wireless: bool = False) -> None:
        super().__init__(running_on_wireless=running_on_wireless)
        self._runtime: HermesVoiceRuntime | None = None
        self._register_settings_routes()

    def _register_settings_routes(self) -> None:
        if self.settings_app is None:
            return

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
            try:
                config = load_config()
                config_payload: dict[str, object] = config.redacted_dict()
                config_error = ""
            except Exception as exc:
                config_payload = {}
                config_error = str(exc)
            runtime_payload = self._runtime.status() if self._runtime is not None else {"state": "not_started"}
            return {
                "app": "reachy_mini_hermes",
                "wake_phrase": "Hey Hermes",
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
        """Run wake detection and serialized Hermes voice turns."""
        self._runtime = HermesVoiceRuntime(reachy_mini, stop_event)
        self._runtime.run()


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
