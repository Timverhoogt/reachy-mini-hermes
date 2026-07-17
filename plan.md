# Reachy Mini Hermes — implementation plan

## Goal

Create a public Reachy Mini Python app that lets a user wake the robot with **“Hey Hermes”**, speak naturally, and converse with their own Hermes Agent instance. The app must follow the Reachy Mini App SDK lifecycle and be publishable in the Reachy Mini/Hugging Face app catalog.

## Product principles

- The robot is the voice and embodiment; Hermes Agent remains the tool-using brain.
- One Reachy setting (`bridge_url` + bearer key) connects to the user's Hermes installation.
- Wake-word, capture, state feedback, and safe motion happen locally on Reachy.
- Agent tools execute on the Hermes API-server host, exactly like other Hermes API clients.
- Secrets are never logged or returned by the settings API.
- Voice states are obvious: wake/listening chime → listening pose → processing chime/thinking pose → speech animation.
- The first release is conservative about motion and supports Reachy Mini Lite and Wireless.

## Architecture

```text
Reachy microphone
  → local open-vocabulary KWS (HEY HERMES)
  → local adaptive VAD / utterance recorder
  → Hermes Reachy Bridge /v1/audio/transcriptions
  → Hermes API Server /v1/chat/completions
  → Hermes Reachy Bridge /v1/audio/speech
  → Reachy speaker + motion state machine
```

### Reachy app

- SDK class: `ReachyMiniHermes(ReachyMiniApp)`.
- Local media backend, audio-only where supported.
- `sherpa-onnx` open-vocabulary English keyword spotter; no custom model training or non-commercial model dependency.
- Keyword asset: `HEY HERMES`, configurable threshold/boost.
- Adaptive energy-based VAD initially, isolated behind an interface for a later Silero VAD backend.
- Hermes client with health checks, timeouts, bearer authentication, stable `X-Hermes-Session-Key`, and rotating `X-Hermes-Session-Id` after inactivity.
- Settings web UI on port 8042.
- Local status endpoint for setup diagnostics.
- Safe, small listening/thinking/speaking poses; no direct LLM motor control in v0.1.

### Hermes companion bridge

A small standalone server runs on the same host/profile as Hermes Agent:

- Proxies authenticated Hermes chat requests to the standard API server.
- Exposes OpenAI-style `/v1/audio/transcriptions` using Hermes' configured STT provider.
- Exposes `/v1/audio/speech` using Hermes' configured TTS provider.
- Uses the same bearer key as the Hermes API server.
- Binds to loopback by default; LAN binding is explicit and documented.
- Does not copy provider credentials to Reachy.

This companion exists because the Hermes API server currently exposes agent chat but not public audio transcription/speech endpoints.

## Configuration defaults

- Bridge URL: `http://<hermes-host>:8643`
- Wake phrase: `HEY HERMES`
- Conversation inactivity timeout: 5 minutes
- Initial speech timeout: 5 seconds
- Maximum utterance: 20 seconds
- End-of-speech silence: 0.8 seconds
- Hermes model field: `hermes-agent` (cosmetic; server chooses the real model)
- TTS response format: MP3
- Continuous conversation: off by default

## Public packaging

- App package/entry point: `reachy_mini_hermes` / `ReachyMiniHermes`.
- README frontmatter includes `reachy_mini_python_app`.
- Apache-2.0 project license.
- Publishable through `reachy-mini-app-assistant publish` after explicit user approval and Hugging Face authentication.
- Wake/earcon assets generated for this project and licensed with it.
- Downloaded third-party KWS model files retain their upstream notices and are cached at runtime rather than committed where practical.

## Test strategy

- Unit tests for config redaction, session continuity, VAD segmentation, duplicate state cues, and HTTP error handling.
- Mock Reachy media/robot test covering wake → record → transcribe → Hermes → TTS → playback.
- Companion bridge tests with mocked Hermes STT/TTS and proxied chat.
- `ruff`, `pytest`, package build, and `reachy-mini-app-assistant check`.
- Simulation/import smoke test without hardware.
- Final physical test on the configured Reachy Mini only after unit/package checks pass.

## Assumptions for v0.1

- Hermes Agent and Reachy are reachable on the same trusted LAN or through a user-managed TLS/VPN proxy.
- The user's Hermes profile has STT and TTS configured.
- English wake phrase detection is the first supported language; agent/STT languages remain provider-configurable.
- Publishing to the user's Hugging Face account is a separate external action and will not happen without confirmation.

## Deferred, not forgotten

- Full duplex/barge-in while TTS is playing.
- Camera frames and robot-specific Hermes tools.
- Local offline STT/TTS fallback on Reachy Wireless.
- Per-user wake-model personalization.
- TLS termination and remote-internet deployment wizard.
