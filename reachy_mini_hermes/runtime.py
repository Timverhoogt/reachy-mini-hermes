"""Wake-to-speech runtime for Reachy Mini Hermes."""

from __future__ import annotations

import logging
import re
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import numpy as np

from .audio import (
    AdaptiveEndpointRecorder,
    EndpointResult,
    NoiseFloor,
    encode_wav,
    mono_float32,
    resample_linear,
)
from .config import AppConfig, load_config
from .hermes_client import HermesBridgeClient, HermesBridgeError, SpeechAudio
from .motion import VoiceMotion
from .wakeword import HeyHermesSpotter, ensure_kws_model

_LOGGER = logging.getLogger(__name__)
_MEDIA_TAG = re.compile(r"(?m)^\s*(?:\[\[audio_as_voice\]\]\s*)?MEDIA:\S+\s*$")
_MARKDOWN = re.compile(r"[`*_#>|]+")


@dataclass(slots=True)
class RuntimeStatus:
    state: str = "starting"
    detail: str = ""
    wake_word: str = "Hey Hermes"
    transcript: str = ""
    response_preview: str = ""
    last_error: str = ""
    bridge_healthy: bool = False
    model_ready: bool = False
    turns_completed: int = 0


class HermesVoiceRuntime:
    """Own microphone capture and serialize voice turns through Hermes."""

    def __init__(
        self,
        robot: object,
        stop_event: threading.Event,
        *,
        config_loader: Callable[[], AppConfig] = load_config,
        assets_directory: Path | None = None,
    ) -> None:
        self.robot = robot
        self.stop_event = stop_event
        self.config_loader = config_loader
        self.assets = assets_directory or Path(__file__).resolve().parent / "assets"
        self._status = RuntimeStatus()
        self._status_lock = threading.RLock()
        self._noise = NoiseFloor()
        self._sample_rate = 16000
        self._motion: VoiceMotion | None = None
        self._spotter: HeyHermesSpotter | None = None
        self._last_wake_at = 0.0

    def status(self) -> dict[str, object]:
        with self._status_lock:
            return asdict(self._status)

    def _set_status(self, state: str, detail: str = "", **updates: object) -> None:
        with self._status_lock:
            self._status.state = state
            self._status.detail = detail
            for key, value in updates.items():
                if hasattr(self._status, key):
                    setattr(self._status, key, value)

    def run(self) -> None:
        self._set_status("starting", "Preparing the Hey Hermes wake-word model")
        config = self.config_loader()
        self._motion = VoiceMotion(self.robot, enabled=config.motion_enabled)
        self._enable_and_wake_robot()
        model_directory = ensure_kws_model()
        self._spotter = HeyHermesSpotter(
            model_directory,
            self.assets / "keywords.txt",
            score=config.wake_keyword_score,
            threshold=config.wake_keyword_threshold,
        )
        self._set_status("starting", "Starting Reachy audio", model_ready=True)

        self.robot.media.start_recording()
        self.robot.media.start_playing()
        time.sleep(0.8)
        detected_rate = int(self.robot.media.get_input_audio_samplerate())
        if detected_rate <= 0:
            raise RuntimeError("Reachy audio input did not report a valid sample rate")
        self._sample_rate = detected_rate
        _LOGGER.info("Reachy Hermes microphone ready at %s Hz", self._sample_rate)

        try:
            self._listen_for_wake_word()
        finally:
            self._set_status("stopping")
            try:
                self.robot.media.stop_recording()
            except Exception:
                _LOGGER.debug("Audio recording was already stopped", exc_info=True)
            try:
                self.robot.media.stop_playing()
            except Exception:
                _LOGGER.debug("Audio playback was already stopped", exc_info=True)
            if self._motion is not None:
                self._motion.idle()

    def _enable_and_wake_robot(self) -> None:
        """Restore motors after another app left the robot asleep.

        Reachy's app manager can leave motor mode disabled when switching apps.
        The daemon REST endpoint is the supported local control surface on
        Wireless hardware; a failed wake remains non-fatal for audio-only use.
        """
        try:
            response = httpx.post(
                "http://127.0.0.1:8000/api/motors/set_mode/enabled", timeout=5.0
            )
            response.raise_for_status()
            self.robot.wake_up()
            _LOGGER.info("Reachy motors enabled and wake motion completed")
        except Exception as exc:
            _LOGGER.warning("Could not enable/wake Reachy motors: %s", exc)

    def _listen_for_wake_word(self) -> None:
        assert self._spotter is not None
        while not self.stop_event.is_set():
            try:
                config = self.config_loader()
            except Exception as exc:
                self._set_status("configuration_error", str(exc), last_error=str(exc))
                self.stop_event.wait(1.0)
                continue

            if not config.configured:
                self._set_status("waiting_for_configuration", "Open the app settings and configure the Hermes bridge")
                self.stop_event.wait(1.0)
                continue

            self._set_status("waiting_for_wake_word", "Say “Hey Hermes”")
            frame = self._read_16k_frame()
            if frame is None:
                continue
            self._noise.update(frame)
            keyword = self._spotter.accept(frame, 16000)
            if not keyword:
                continue
            now = time.monotonic()
            if now - self._last_wake_at < config.wake_cooldown_seconds:
                continue
            self._last_wake_at = now
            _LOGGER.info("Wake word detected: %s", keyword)
            try:
                self._run_conversation(config)
            except Exception as exc:
                _LOGGER.exception("Reachy Hermes voice turn failed")
                self._set_status("error", str(exc), last_error=str(exc))
                self._signal_error()
                self.stop_event.wait(0.4)
            finally:
                self._spotter.reset()
                if self._motion is not None:
                    self._motion.idle()

    def _run_conversation(self, initial_config: AppConfig) -> None:
        config = initial_config
        client = HermesBridgeClient(config)
        try:
            health = client.health()
            self._set_status("listening", "Wake word accepted", bridge_healthy=True, last_error="")
            _LOGGER.debug("Hermes bridge health: %s", health.get("status"))

            first_turn = True
            while not self.stop_event.is_set():
                if not first_turn:
                    config = self.config_loader()
                    if not config.continuous_conversation:
                        break
                    self._set_status("listening", "Waiting for a follow-up")
                first_turn = False

                self._play_asset("listening.wav")
                if self._motion is not None:
                    self._motion.listening()
                # Keep draining the microphone while the local earcon plays so
                # its samples cannot become the start of the user's utterance.
                self._discard_audio(0.34)

                endpoint = self._record_utterance(config)
                if not endpoint.speech_detected or endpoint.samples.size == 0:
                    self._set_status("waiting_for_wake_word", "No speech detected")
                    if not config.continuous_conversation:
                        self._signal_error()
                    break

                self._play_asset("processing.wav")
                if self._motion is not None:
                    self._motion.thinking()
                self._set_status("transcribing", "Command received; transcribing")
                transcript = client.transcribe(encode_wav(endpoint.samples, 16000))
                _LOGGER.info("Transcript: %s", transcript)
                self._set_status("thinking", "Hermes is working", transcript=transcript)

                response_text = client.chat(transcript)
                spoken_text = self._speech_friendly(response_text)
                self._set_status(
                    "synthesizing",
                    "Generating speech",
                    response_preview=response_text[:240],
                )
                speech = client.synthesize(spoken_text)
                self._play_response(speech, spoken_text)
                with self._status_lock:
                    self._status.turns_completed += 1
                if not config.continuous_conversation:
                    break
        except HermesBridgeError:
            self._set_status("error", "Hermes bridge request failed", bridge_healthy=False)
            raise
        finally:
            client.close()

    def _record_utterance(self, config: AppConfig) -> EndpointResult:
        recorder = AdaptiveEndpointRecorder(
            initial_timeout=config.initial_speech_timeout_seconds,
            max_duration=config.max_utterance_seconds,
            end_silence=config.end_silence_seconds,
            minimum_rms=config.vad_min_rms,
            noise_multiplier=config.vad_noise_multiplier,
        )
        result = recorder.record(
            self._read_16k_frame,
            noise_floor=self._noise.value,
            should_stop=self.stop_event.is_set,
        )
        _LOGGER.info(
            "Speech endpoint: reason=%s speech=%s duration=%.2fs threshold=%.4f",
            result.reason,
            result.speech_detected,
            result.samples.size / 16000.0,
            result.threshold,
        )
        return result

    def _read_16k_frame(self) -> np.ndarray | None:
        raw = self.robot.media.get_audio_sample()
        if raw is None:
            time.sleep(0.002)
            return None
        normalized = mono_float32(raw)
        return resample_linear(normalized, self._sample_rate, 16000)

    def _discard_audio(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self.stop_event.is_set():
            self._read_16k_frame()

    def _play_asset(self, name: str) -> None:
        path = self.assets / name
        if path.exists():
            self.robot.media.play_sound(str(path))

    def _signal_error(self) -> None:
        self._play_asset("error.wav")
        if self._motion is not None:
            self._motion.error()

    def _play_response(self, speech: SpeechAudio, text: str) -> None:
        suffix = speech.extension if speech.extension.startswith(".") else ".audio"
        with tempfile.NamedTemporaryFile(prefix="reachy-hermes-response-", suffix=suffix, delete=False) as output:
            output.write(speech.data)
            path = Path(output.name)
        try:
            duration = self._audio_duration(path, fallback_text=text)
            self._set_status("speaking", "Hermes is speaking")
            if self._motion is not None:
                self._motion.speaking()
            self.robot.media.play_sound(str(path))
            deadline = time.monotonic() + duration + 0.15
            while time.monotonic() < deadline and not self.stop_event.is_set():
                time.sleep(0.05)
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    @staticmethod
    def _audio_duration(path: Path, *, fallback_text: str) -> float:
        try:
            from mutagen import File as MutagenFile

            media = MutagenFile(path)
            if media is not None and media.info is not None:
                return max(0.1, min(float(media.info.length), 120.0))
        except Exception:
            _LOGGER.debug("Could not inspect TTS duration", exc_info=True)
        return max(1.0, min(len(fallback_text) / 13.0 + 0.6, 45.0))

    @staticmethod
    def _speech_friendly(text: str) -> str:
        text = _MEDIA_TAG.sub("", text)
        text = _MARKDOWN.sub("", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text or "I completed the request, but there is no spoken response."
