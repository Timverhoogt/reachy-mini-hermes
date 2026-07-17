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
from .realtime_client import RealtimeBridgeError, RealtimeBridgeSession
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
    stt_provider: str = ""
    tts_provider: str = ""
    audio_rms: float = 0.0
    audio_peak: float = 0.0
    audio_frames_processed: int = 0
    power_mode: str = "standby"
    meeting_seconds_remaining: int = 0
    interruptions: int = 0


@dataclass(slots=True)
class RealtimePlayback:
    """Track audio that may still be buffered after generation has finished."""

    item_id: str = ""
    started_at: float | None = None
    queued_until: float = 0.0
    duration_seconds: float = 0.0

    def add(self, now: float, duration_seconds: float) -> None:
        if self.started_at is None or now >= self.queued_until:
            self.started_at = now
            self.queued_until = now
            self.duration_seconds = 0.0
        self.duration_seconds += duration_seconds
        self.queued_until = max(now, self.queued_until) + duration_seconds

    def audible(self, now: float) -> bool:
        return self.started_at is not None and now < self.queued_until

    def played_ms(self, now: float) -> int:
        if self.started_at is None:
            return 0
        elapsed = max(0.0, now - self.started_at)
        return int(min(elapsed, self.duration_seconds) * 1000.0)

    def reset(self) -> None:
        self.item_id = ""
        self.started_at = None
        self.queued_until = 0.0
        self.duration_seconds = 0.0


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
        self._output_sample_rate = 48000
        self._motion: VoiceMotion | None = None
        self._spotter: HeyHermesSpotter | None = None
        self._last_wake_at = 0.0
        self._power_lock = threading.RLock()
        self._power_mode = "standby"
        self._meeting_until = 0.0
        self._recording = False

    def set_power_mode(self, mode: str, *, duration_seconds: float = 0.0) -> dict[str, object]:
        """Change the voice lifecycle without stopping the settings service."""
        mode = mode.strip().lower()
        if mode not in {"standby", "awake", "meeting", "sleep"}:
            raise ValueError(f"Unsupported power mode: {mode}")
        with self._power_lock:
            self._power_mode = mode
            self._meeting_until = (
                time.monotonic() + max(60.0, min(duration_seconds, 8 * 3600.0))
                if mode == "meeting"
                else 0.0
            )
        self._apply_power_mode()
        return self.status()

    def _effective_power_mode(self) -> str:
        with self._power_lock:
            if self._power_mode == "meeting" and time.monotonic() >= self._meeting_until:
                self._power_mode = "standby"
                self._meeting_until = 0.0
            return self._power_mode

    def _apply_power_mode(self) -> None:
        mode = self._effective_power_mode()
        remaining = 0
        with self._power_lock:
            if mode == "meeting":
                remaining = max(0, int(self._meeting_until - time.monotonic()))
        if mode in {"meeting", "sleep"}:
            try:
                self.robot.media.play_sound(str(self.assets / "silence.wav"))
            except Exception:
                _LOGGER.debug("No active playback to stop", exc_info=True)
            self._clear_streamed_audio()
            if self._recording:
                try:
                    self.robot.media.stop_recording()
                finally:
                    self._recording = False
            self._set_motor_mode(False)
            self._set_status(
                mode,
                "Voice and motion are disabled",
                power_mode=mode,
                meeting_seconds_remaining=remaining,
            )
            return
        if not self._recording:
            self.robot.media.start_recording()
            self._recording = True
        if mode == "standby":
            self._set_motor_mode(False)
            self._set_status(
                "waiting_for_wake_word",
                "Local wake detection only",
                power_mode=mode,
                meeting_seconds_remaining=0,
            )
        else:
            self._set_motor_mode(True, wake=False)
            self._set_status(
                "waiting_for_wake_word",
                "Say “Hey Hermes”",
                power_mode=mode,
                meeting_seconds_remaining=0,
            )

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
        model_directory = ensure_kws_model()
        self._spotter = HeyHermesSpotter(
            model_directory,
            self.assets / "keywords.txt",
            score=config.wake_keyword_score,
            threshold=config.wake_keyword_threshold,
        )
        self._set_status("starting", "Starting Reachy audio", model_ready=True)

        self.robot.media.start_recording()
        self._recording = True
        self.robot.media.start_playing()
        time.sleep(0.8)
        detected_rate = int(self.robot.media.get_input_audio_samplerate())
        if detected_rate <= 0:
            raise RuntimeError("Reachy audio input did not report a valid sample rate")
        self._sample_rate = detected_rate
        output_rate = int(self.robot.media.get_output_audio_samplerate())
        if output_rate > 0:
            self._output_sample_rate = output_rate
        _LOGGER.info(
            "Reachy Hermes audio ready: input=%s Hz output=%s Hz",
            self._sample_rate,
            self._output_sample_rate,
        )
        self._apply_power_mode()

        try:
            self._listen_for_wake_word()
        finally:
            self._set_status("stopping")
            try:
                if self._recording:
                    self.robot.media.stop_recording()
                    self._recording = False
            except Exception:
                _LOGGER.debug("Audio recording was already stopped", exc_info=True)
            try:
                self.robot.media.stop_playing()
            except Exception:
                _LOGGER.debug("Audio playback was already stopped", exc_info=True)
            if self._motion is not None:
                self._motion.idle()

    def _set_motor_mode(self, enabled: bool, *, wake: bool = False) -> None:
        """Change motor torque through the daemon's supported local API."""
        mode = "enabled" if enabled else "disabled"
        try:
            response = httpx.post(
                f"http://127.0.0.1:8000/api/motors/set_mode/{mode}", timeout=5.0
            )
            response.raise_for_status()
            if enabled and wake:
                self.robot.wake_up()
            _LOGGER.info("Reachy motors %s%s", mode, " with wake motion" if wake else "")
        except Exception as exc:
            _LOGGER.warning("Could not set Reachy motors to %s: %s", mode, exc)

    def _listen_for_wake_word(self) -> None:
        assert self._spotter is not None
        while not self.stop_event.is_set():
            try:
                config = self.config_loader()
            except Exception as exc:
                self._set_status("configuration_error", str(exc), last_error=str(exc))
                self.stop_event.wait(1.0)
                continue

            mode = self._effective_power_mode()
            with self._status_lock:
                applied_mode = self._status.power_mode
            if mode != applied_mode:
                self._apply_power_mode()
            if mode in {"meeting", "sleep"}:
                if mode == "meeting":
                    with self._power_lock:
                        remaining = max(0, int(self._meeting_until - time.monotonic()))
                    with self._status_lock:
                        self._status.meeting_seconds_remaining = remaining
                self.stop_event.wait(0.25)
                continue

            if not config.configured:
                self._set_status("waiting_for_configuration", "Open the app settings and configure the Hermes bridge")
                self.stop_event.wait(1.0)
                continue

            detail = "Local wake detection only" if mode == "standby" else "Say “Hey Hermes”"
            self._set_status("waiting_for_wake_word", detail)
            frame = self._read_16k_frame()
            if frame is None:
                continue
            rms = float(np.sqrt(np.mean(np.square(frame), dtype=np.float64)))
            peak = float(np.max(np.abs(frame))) if frame.size else 0.0
            with self._status_lock:
                self._status.audio_rms = rms
                self._status.audio_peak = peak
                self._status.audio_frames_processed += 1
            self._noise.update(frame)
            keyword = self._spotter.accept(frame, 16000)
            if not keyword:
                continue
            now = time.monotonic()
            if now - self._last_wake_at < config.wake_cooldown_seconds:
                continue
            self._last_wake_at = now
            _LOGGER.info("Wake word detected: %s", keyword)
            self._set_motor_mode(True, wake=True)
            try:
                if config.conversation_mode == "realtime":
                    self._run_realtime_conversation(config)
                else:
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
                if self._effective_power_mode() == "standby":
                    self._set_motor_mode(False)
    def _run_realtime_conversation(self, config: AppConfig) -> None:
        """Run a persistent speech-to-speech session after the local wake word."""
        session = RealtimeBridgeSession(config)
        transcript_parts: list[str] = []
        response_parts: list[str] = []
        last_activity = time.monotonic()
        speaking = False
        generation_done = False
        playback = RealtimePlayback()
        self._play_asset("listening.wav")
        self._discard_audio(0.34)
        self._set_status(
            "connecting_realtime",
            "Opening private GPT Realtime session",
            bridge_healthy=True,
            last_error="",
        )
        session.start()
        self._set_status("listening", "Realtime session active")
        if self._motion is not None:
            self._motion.listening()
        try:
            while not self.stop_event.is_set():
                if self._effective_power_mode() in {"meeting", "sleep"}:
                    break
                if time.monotonic() - last_activity >= config.conversation_timeout_seconds:
                    _LOGGER.info("Realtime conversation closed after inactivity timeout")
                    break

                frame = self._read_16k_frame()
                if frame is not None:
                    session.send_audio(resample_linear(frame, 16000, 24000))

                for event in session.events():
                    kind = event.type
                    payload = event.payload
                    if kind in {"bridge.error", "error"}:
                        error = payload.get("error")
                        if isinstance(error, dict):
                            error = error.get("message") or error
                        raise RealtimeBridgeError(str(error or "Realtime session failed"))
                    if kind == "input_audio_buffer.speech_started":
                        now = time.monotonic()
                        last_activity = now
                        transcript_parts.clear()
                        if speaking or playback.audible(now):
                            played_ms = playback.played_ms(now)
                            self._clear_streamed_audio()
                            if playback.item_id:
                                session.truncate_audio(playback.item_id, played_ms)
                            _LOGGER.info(
                                "Realtime interruption: cleared buffered audio at %s ms",
                                played_ms,
                            )
                            speaking = False
                            generation_done = False
                            playback.reset()
                            with self._status_lock:
                                self._status.interruptions += 1
                        self._set_status("listening", "Listening to interruption")
                        if self._motion is not None:
                            self._motion.listening()
                    elif kind in {
                        "conversation.item.input_audio_transcription.delta",
                        "conversation.item.input_audio_transcription.completed",
                    }:
                        text = str(payload.get("delta") or payload.get("transcript") or "")
                        if text:
                            if kind.endswith(".delta"):
                                transcript_parts.append(text)
                            else:
                                transcript_parts = [text]
                            self._set_status(
                                "thinking",
                                "Hermes is responding",
                                transcript="".join(transcript_parts).strip(),
                                stt_provider="openai-realtime",
                            )
                    elif kind == "response.created":
                        last_activity = time.monotonic()
                        generation_done = False
                        playback.reset()
                        self._set_status("thinking", "Hermes is responding")
                        if self._motion is not None:
                            self._motion.thinking()
                    elif kind == "response.output_item.added":
                        item = payload.get("item")
                        if isinstance(item, dict):
                            playback.item_id = str(item.get("id") or "")
                        last_activity = time.monotonic()
                        self._set_status("thinking", "Hermes is responding")
                    elif kind in {"response.output_audio.delta", "response.audio.delta"}:
                        audio = session.audio_samples(event)
                        if audio.size:
                            now = time.monotonic()
                            if not speaking:
                                speaking = True
                                response_parts.clear()
                                self._set_status(
                                    "speaking",
                                    "Hermes Realtime is speaking",
                                    tts_provider="openai-realtime",
                                )
                                if self._motion is not None:
                                    self._motion.speaking()
                            output = resample_linear(audio, 24000, self._output_sample_rate)
                            self.robot.media.push_audio_sample(output)
                            playback.add(now, output.size / self._output_sample_rate)
                            last_activity = now
                    elif kind in {
                        "response.output_audio_transcript.delta",
                        "response.audio_transcript.delta",
                    }:
                        response_parts.append(str(payload.get("delta") or ""))
                        self._set_status(
                            "speaking",
                            "Hermes Realtime is speaking",
                            response_preview="".join(response_parts)[-240:],
                        )
                    elif kind in {"response.done", "response.output_audio.done", "response.audio.done"}:
                        if kind == "response.done":
                            with self._status_lock:
                                self._status.turns_completed += 1
                            generation_done = True
                        last_activity = time.monotonic()
                if generation_done and speaking and not playback.audible(time.monotonic()):
                    speaking = False
                    generation_done = False
                    playback.reset()
                    self._set_status("listening", "Waiting for a follow-up")
                    if self._motion is not None:
                        self._motion.listening()
        finally:
            session.close()
            self._clear_streamed_audio()

    def _clear_streamed_audio(self) -> None:
        """Flush Realtime appsrc output without stopping microphone capture."""
        audio = getattr(self.robot.media, "audio", None)
        clear = getattr(audio, "clear_player", None)
        if callable(clear):
            clear()

    def _run_conversation(self, initial_config: AppConfig) -> None:
        config = initial_config
        client = HermesBridgeClient(config)
        try:
            health = client.health()
            self._set_status("listening", "Wake word accepted", bridge_healthy=True, last_error="")
            _LOGGER.debug("Hermes bridge health: %s", health.get("status"))

            first_turn = True
            while not self.stop_event.is_set():
                if self._effective_power_mode() in {"meeting", "sleep"}:
                    break
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
                if self._effective_power_mode() in {"meeting", "sleep"}:
                    break
                _LOGGER.info("Transcript: %s", transcript)
                self._set_status(
                    "thinking",
                    "Hermes is working",
                    transcript=transcript,
                    stt_provider=client.last_stt_provider,
                )

                response_text = client.chat(transcript)
                if self._effective_power_mode() in {"meeting", "sleep"}:
                    break
                spoken_text = self._speech_friendly(response_text)
                self._set_status(
                    "synthesizing",
                    "Generating speech",
                    response_preview=response_text[:240],
                )
                speech = client.synthesize(spoken_text)
                self._set_status("speaking", "Reachy is speaking", tts_provider=speech.provider)
                interrupted = self._play_response(speech, spoken_text, barge_in=config.barge_in_enabled)
                if self._effective_power_mode() in {"meeting", "sleep"}:
                    break
                with self._status_lock:
                    self._status.turns_completed += 1
                if interrupted:
                    first_turn = False
                    continue
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

    def _play_response(self, speech: SpeechAudio, text: str, *, barge_in: bool = True) -> bool:
        """Play a response and allow a local “Hey Hermes” barge-in.

        Pipeline mode cannot safely use open-mic RMS detection because Reachy's
        speaker is audible to its microphone. Reusing the local wake spotter
        avoids self-interruption; Realtime mode provides natural semantic VAD.
        """
        suffix = speech.extension if speech.extension.startswith(".") else ".audio"
        with tempfile.NamedTemporaryFile(prefix="reachy-hermes-response-", suffix=suffix, delete=False) as output:
            output.write(speech.data)
            path = Path(output.name)
        interrupted = False
        try:
            duration = self._audio_duration(path, fallback_text=text)
            self._set_status("speaking", "Hermes is speaking")
            if self._motion is not None:
                self._motion.speaking()
            if self._spotter is not None:
                self._spotter.reset()
            self.robot.media.play_sound(str(path))
            deadline = time.monotonic() + duration + 0.15
            while time.monotonic() < deadline and not self.stop_event.is_set():
                if self._effective_power_mode() in {"meeting", "sleep"}:
                    break
                if not barge_in or self._spotter is None:
                    time.sleep(0.02)
                    continue
                frame = self._read_16k_frame()
                if frame is None:
                    continue
                keyword = self._spotter.accept(frame, 16000)
                if not keyword:
                    continue
                interrupted = True
                self.robot.media.play_sound(str(self.assets / "silence.wav"))
                self._spotter.reset()
                with self._status_lock:
                    self._status.interruptions += 1
                self._set_status("listening", "Response interrupted; listening")
                if self._motion is not None:
                    self._motion.listening()
                _LOGGER.info("Playback interrupted by local wake phrase: %s", keyword)
                break
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        return interrupted

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
