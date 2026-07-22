"""Persistent configuration for the Reachy Mini Hermes app."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlparse

_CONFIG_LOCK = threading.RLock()
_CONFIG_ENV = "REACHY_MINI_HERMES_CONFIG"


def default_config_path() -> Path:
    """Return a user-writable path that survives package upgrades."""
    override = os.getenv(_CONFIG_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "reachy_mini_hermes" / "config.json"


@dataclass(slots=True)
class AppConfig:
    """Runtime settings. Secrets are only exposed through ``redacted_dict``."""

    bridge_url: str = "http://127.0.0.1:8643"
    api_key: str = ""
    model: str = "hermes-agent"
    conversation_mode: str = "pipeline"
    capability_profile: str = "conversation"
    language: str = "en"
    stt_provider: str = "configured"
    stt_model: str = "base"
    tts_provider: str = "configured"
    tts_model: str = "eleven_flash_v2_5"
    tts_voice: str = "pNInz6obpgDQGcFmaJgB"
    system_prompt: str = (
        "You are speaking through a Reachy Mini robot. Reply naturally and concisely for speech. "
        "Avoid Markdown tables, long lists, file paths, and MEDIA tags unless the user asks for them."
    )
    continuous_conversation: bool = False
    conversation_timeout_seconds: float = 300.0
    initial_speech_timeout_seconds: float = 5.0
    max_utterance_seconds: float = 20.0
    end_silence_seconds: float = 0.8
    vad_min_rms: float = 0.012
    vad_noise_multiplier: float = 3.0
    wake_keyword_score: float = 1.5
    wake_keyword_threshold: float = 0.25
    wake_cooldown_seconds: float = 2.0
    motion_enabled: bool = True
    barge_in_enabled: bool = True
    camera_enabled: bool = False
    camera_feed_enabled: bool = False
    face_tracking_enabled: bool = False
    face_tracking_weight: float = 0.65
    doa_enabled: bool = False
    robot_tools_enabled: bool = True
    gamepad_enabled: bool = False
    agent_tools_enabled: bool = True
    power_tools_enabled: bool = True
    kids_parent_pin_hash: str = ""
    kids_mode_enabled: bool = False
    kids_session_id: str = ""
    kids_age_band: str = ""
    kids_activity: str = ""
    realtime_model: str = "gpt-realtime-2.1"
    realtime_voice: str = "marin"
    realtime_reasoning_effort: str = "low"
    instance_id: str = ""

    def __post_init__(self) -> None:
        self.bridge_url = self.bridge_url.strip().rstrip("/")
        self.api_key = self.api_key.strip()
        self.model = self.model.strip() or "hermes-agent"
        self.conversation_mode = self.conversation_mode.strip().lower() or "pipeline"
        self.capability_profile = self.capability_profile.strip().lower() or "conversation"
        self.language = self.language.strip() or "en"
        self.stt_provider = self.stt_provider.strip().lower() or "configured"
        self.stt_model = self.stt_model.strip() or "base"
        self.tts_provider = self.tts_provider.strip().lower() or "configured"
        self.tts_model = self.tts_model.strip() or "eleven_flash_v2_5"
        self.tts_voice = self.tts_voice.strip() or "pNInz6obpgDQGcFmaJgB"
        self.realtime_model = self.realtime_model.strip() or "gpt-realtime-2.1"
        self.realtime_voice = self.realtime_voice.strip() or "marin"
        self.realtime_reasoning_effort = self.realtime_reasoning_effort.strip().lower() or "low"
        self.kids_parent_pin_hash = self.kids_parent_pin_hash.strip()
        self.kids_session_id = self.kids_session_id.strip()
        self.kids_age_band = self.kids_age_band.strip()
        self.kids_activity = self.kids_activity.strip()
        if not self.instance_id:
            self.instance_id = uuid.uuid4().hex
        self.validate()

    def validate(self) -> None:
        parsed = urlparse(self.bridge_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("bridge_url must be an absolute http(s) URL")
        if not 0.01 <= float(self.wake_keyword_threshold) <= 1.0:
            raise ValueError("wake_keyword_threshold must be between 0.01 and 1.0")
        if not 0.1 <= float(self.end_silence_seconds) <= 5.0:
            raise ValueError("end_silence_seconds must be between 0.1 and 5.0")
        if not 1.0 <= float(self.max_utterance_seconds) <= 120.0:
            raise ValueError("max_utterance_seconds must be between 1 and 120")
        if not 1.0 <= float(self.initial_speech_timeout_seconds) <= 30.0:
            raise ValueError("initial_speech_timeout_seconds must be between 1 and 30")
        if not 0.001 <= float(self.vad_min_rms) <= 0.5:
            raise ValueError("vad_min_rms must be between 0.001 and 0.5")
        if not 0.0 <= float(self.face_tracking_weight) <= 1.0:
            raise ValueError("face_tracking_weight must be between 0 and 1")
        if self.stt_provider not in {"configured", "local", "elevenlabs"}:
            raise ValueError("Unsupported STT provider")
        if self.tts_provider not in {"configured", "elevenlabs"}:
            raise ValueError("Unsupported TTS provider")
        if self.conversation_mode not in {"pipeline", "realtime"}:
            raise ValueError("Unsupported conversation mode")
        if self.capability_profile not in {"conversation", "agent"}:
            raise ValueError("Unsupported capability profile")
        if self.realtime_reasoning_effort not in {"minimal", "low", "medium", "high", "xhigh"}:
            raise ValueError("Unsupported realtime reasoning effort")
        if self.kids_mode_enabled:
            if self.kids_age_band not in {"4-6", "7-9", "10-12"}:
                raise ValueError("Unsupported Kids Mode age band")
            if self.kids_activity not in {"buddy", "story", "quiz", "riddles", "calm", "ispy"}:
                raise ValueError("Unsupported Kids Mode activity")

    @property
    def configured(self) -> bool:
        return bool(self.bridge_url and self.api_key)

    def child_status_dict(self) -> dict[str, object]:
        """Return only non-sensitive readiness flags for the locked child UI."""
        return {
            "configured": self.configured,
            "api_key_configured": bool(self.api_key),
            "kids_parent_pin_configured": bool(self.kids_parent_pin_hash),
        }

    def redacted_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["api_key"] = "********" if self.api_key else ""
        payload["api_key_configured"] = bool(self.api_key)
        payload.pop("kids_parent_pin_hash", None)
        payload.pop("kids_session_id", None)
        payload.pop("kids_mode_enabled", None)
        payload.pop("kids_age_band", None)
        payload.pop("kids_activity", None)
        payload["kids_parent_pin_configured"] = bool(self.kids_parent_pin_hash)
        return payload


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or default_config_path()
    with _CONFIG_LOCK:
        if not config_path.exists():
            return AppConfig()
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("configuration root must be an object")
            allowed = {f.name for f in fields(AppConfig)}
            return AppConfig(**{key: value for key, value in payload.items() if key in allowed})
        except Exception as exc:
            raise ValueError(f"Could not load {config_path}: {exc}") from exc


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    config.validate()
    config_path = path or default_config_path()
    with _CONFIG_LOCK:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = config_path.with_suffix(config_path.suffix + ".tmp")
        temporary.write_text(json.dumps(asdict(config), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(config_path)
        os.chmod(config_path, 0o600)
    return config_path


def merge_config(current: AppConfig, updates: dict[str, object]) -> AppConfig:
    """Merge settings while treating a masked API key as "keep existing"."""
    allowed = {f.name for f in fields(AppConfig)}
    payload = asdict(current)
    for key, value in updates.items():
        if key not in allowed or key == "instance_id":
            continue
        if key == "api_key" and isinstance(value, str) and (not value or set(value) == {"*"}):
            continue
        payload[key] = value
    return AppConfig(**payload)
