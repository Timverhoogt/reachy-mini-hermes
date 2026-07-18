from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from reachy_mini_hermes.config import AppConfig
from reachy_mini_hermes.kids_mode import (
    KidsProfile,
    build_kids_prompt,
    hash_parent_pin,
    kids_greeting,
    verify_parent_pin,
)
from reachy_mini_hermes.runtime import HermesVoiceRuntime

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "reachy_mini_hermes" / "static"


class FakeMedia:
    pass


class FakeRobot:
    def __init__(self) -> None:
        self.media = FakeMedia()


def test_kids_profile_is_bounded_and_prompt_has_core_safeguards() -> None:
    profile = KidsProfile(
        nickname="  Sam  ",
        age_band="4-6",
        activity="story",
        language="nl",
        duration_minutes=15,
        motion_enabled=False,
    )

    prompt = build_kids_prompt(profile)
    assert profile.nickname == "Sam"
    assert "Dutch" in prompt
    assert "aged 4-6" in prompt
    assert "Never ask for or repeat a full name" in prompt
    assert "Do not suggest" in prompt and "keeping secrets" in prompt
    assert "Camera access is disabled" in prompt
    assert "trusted grown-up" in prompt
    assert "Current activity:" in prompt
    assert kids_greeting(profile).startswith("Hi Sam.")


def test_parent_pin_uses_salted_scrypt_and_never_round_trips_plaintext() -> None:
    first = hash_parent_pin("4826")
    second = hash_parent_pin("4826")
    assert first.startswith("scrypt$")
    assert first != second
    assert "4826" not in first
    assert verify_parent_pin("4826", first) is True
    assert verify_parent_pin("4827", first) is False
    with pytest.raises(ValueError, match="4 to 8 digits"):
        hash_parent_pin("123")


def test_pin_verifier_is_removed_from_redacted_status() -> None:
    config = AppConfig(kids_parent_pin_hash=hash_parent_pin("4826"))
    redacted = config.redacted_dict()
    assert "kids_parent_pin_hash" not in redacted
    assert redacted["kids_parent_pin_configured"] is True


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"age_band": "13-15"}, "age band"),
        ({"activity": "internet"}, "activity"),
        ({"language": "de"}, "language"),
        ({"duration_minutes": 4}, "duration"),
        ({"duration_minutes": 121}, "duration"),
        ({"nickname": "x" * 33}, "nickname"),
    ],
)
def test_kids_profile_rejects_unbounded_values(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=f"(?i){message}"):
        KidsProfile(**kwargs)


def test_runtime_kids_mode_forces_moderated_pipeline_and_removes_private_tools() -> None:
    runtime = HermesVoiceRuntime(FakeRobot(), threading.Event())
    runtime._audio_ready = True
    profile = KidsProfile(activity="quiz", duration_minutes=15, motion_enabled=True)
    config = AppConfig(
        conversation_mode="realtime",
        camera_enabled=True,
        camera_feed_enabled=True,
        face_tracking_enabled=True,
        doa_enabled=True,
    )

    status = runtime.start_kids_mode(profile, greet=False)
    effective = runtime._kids_voice_config(config)

    assert status["active"] is True
    assert status["locked"] is True
    public_profile = status["profile"]
    assert isinstance(public_profile, dict)
    assert "nickname" not in public_profile
    assert status["tool_policy"] == "voice-state-motion-only"
    assert effective.conversation_mode == "pipeline"
    assert effective.kids_mode_enabled is True
    assert effective.kids_session_id.startswith("kids-")
    assert effective.camera_enabled is False
    assert effective.camera_feed_enabled is False
    assert effective.face_tracking_enabled is False
    assert effective.doa_enabled is False
    assert effective.agent_tools_enabled is False
    assert effective.power_tools_enabled is False
    assert effective.robot_tools_enabled is False
    assert effective.continuous_conversation is True
    assert effective.system_prompt != config.system_prompt
    current_generation = runtime._kids_generation
    runtime._expire_kids_mode(current_generation - 1)
    assert runtime.status()["kids_mode"]["active"] is True  # type: ignore[index]

    stopped = runtime.stop_kids_mode(fold=False)
    assert stopped["active"] is False
    assert stopped["locked"] is True
    assert stopped["last_end_reason"] == "parent"
    unlocked = runtime.unlock_kids_controls()
    assert unlocked["locked"] is False


def test_runtime_pushes_kids_pcm_chunks_directly_to_reachy_audio() -> None:
    pushed: list[np.ndarray] = []

    class StreamingMedia:
        def push_audio_sample(self, sample: np.ndarray) -> None:
            pushed.append(sample.copy())

    robot = SimpleNamespace(media=StreamingMedia())
    runtime = HermesVoiceRuntime(robot, threading.Event())
    runtime._output_sample_rate = 24000

    class StreamingClient:
        last_tts_provider = "elevenlabs-flash-stream"

        @staticmethod
        def iter_kids_speech(_text: str):
            yield b"\x00"
            yield b"\x00\xff\x7f"

    interrupted = runtime._play_kids_stream(StreamingClient(), "Hello", barge_in=False)  # type: ignore[arg-type]

    assert interrupted is False
    assert pushed
    samples = np.concatenate(pushed)
    assert samples.shape == (2,)
    assert samples[0] == pytest.approx(0.0)
    assert samples[1] > 0.99


def test_kids_tab_has_activities_parent_controls_disclosures_and_end_button() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    main = (STATIC / "main.js").read_text(encoding="utf-8")
    style = (STATIC / "style.css").read_text(encoding="utf-8")
    backend = (ROOT / "reachy_mini_hermes" / "main.py").read_text(encoding="utf-8")

    assert 'data-tab="kids"' in html
    assert 'data-panel="kids"' in html
    for activity in ("buddy", "story", "quiz", "riddles", "calm"):
        assert f'data-kids-activity="{activity}"' in html
    assert 'id="kids-age-band"' in html
    assert 'id="kids-duration"' in html
    assert 'id="kids-motion-enabled"' in html
    assert 'id="kids-stop-button"' in html
    assert 'id="kids-parent-pin"' in html
    assert 'id="kids-pin-setup-button"' in html
    assert 'id="kids-parent-unlock-button"' in html
    assert "Adult supervision" in html
    assert "No private tools" in html
    assert 'fetch("/api/kids/start"' in main
    assert 'fetch("/api/kids/stop"' in main
    assert '"/api/kids/parent/setup"' in main
    assert '"/api/kids/parent/unlock"' in main
    assert "reachy-hermes-kids-profile" in main
    persisted_profile = main.split(
        'window.localStorage.setItem("reachy-hermes-kids-profile"',
        1,
    )[1].split(";", 1)[0]
    assert "parent_pin" not in persisted_profile
    assert "enabled: !kidsActive" in main
    assert ".kids-activities" in style
    assert 'post("/api/kids/start")' in backend
    assert 'post("/api/kids/stop")' in backend


def test_kids_static_assets_advance_pwa_cache_together() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    worker = (STATIC / "service-worker.js").read_text(encoding="utf-8")
    assert "reachy-hermes-shell-v18" in worker
    for asset in ("style.css", "camera.js", "main.js"):
        assert f"/static/{asset}?v=18" in html
        assert f'"/static/{asset}?v=18"' in worker
