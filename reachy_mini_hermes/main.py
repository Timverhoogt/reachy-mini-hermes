"""Reachy Mini App SDK entry point for Hermes voice conversations."""

from __future__ import annotations

import logging
import threading

from fastapi import HTTPException
from pydantic import BaseModel, Field
from reachy_mini import ReachyMini, ReachyMiniApp

from .config import AppConfig, load_config, merge_config, save_config
from .hermes_client import HermesBridgeClient
from .runtime import HermesVoiceRuntime

_LOGGER = logging.getLogger(__name__)


class SettingsUpdate(BaseModel):
    """A deliberately bounded settings payload for the app UI."""

    bridge_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    language: str | None = None
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
