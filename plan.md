# Reachy Mini Hermes — implementation plan and status

## Goal

Create a public Reachy Mini Python app that wakes on **“Hey Hermes”**, supports natural low-latency conversation, and preserves access to the user's Hermes identity, memory, skills, tools, and Home Assistant environment.

## Product principles

- Wake-word processing remains local.
- Provider credentials remain on the Hermes host.
- Reachy receives one bridge URL and one private bearer token.
- Ordinary conversation should be fast; tool-using requests may delegate to the full Hermes agent.
- Cloud audio starts only after wake detection.
- Voice and power states must be visible and reversible.
- Motor torque is disabled when physical movement is unnecessary.
- No LLM receives direct motor-control primitives in v0.1.

## Implemented architecture

### Realtime mode

```text
Reachy local KWS
  → private companion WebSocket
  → OpenAI gpt-realtime-2.1
      ↳ ask_hermes function
          → Hermes API Server
          → memory, tools, Home Assistant, files, current information
  → streamed audio + Reachy motion
```

Implemented:

- OpenAI GA Realtime WebSocket protocol;
- 24 kHz PCM transport and device-rate resampling;
- semantic VAD and interruption handling;
- Marin and Cedar voices;
- minimal through extra-high reasoning effort;
- streamed transcript, audio, and status events;
- `ask_hermes` function-call round trip;
- on-demand `capture_reachy_camera` tool and Realtime image input;
- credentials isolated to the Hermes host.

### Pipeline mode

```text
Reachy local KWS
  → local endpointing
  → selected STT
  → Hermes API Server agent
  → selected TTS
  → Reachy playback + motion
```

Implemented:

- configured and ElevenLabs STT/TTS selection;
- model and account-voice discovery;
- configurable Hermes agent routes;
- continued conversation;
- wake-phrase interruption during playback;
- stable memory scope and rotating conversation IDs.

### Power lifecycle

Implemented states:

- **Standby:** local wake processing, motors disabled;
- **Awake:** local wake processing, motors enabled;
- **Meeting:** timed microphone stop and motors disabled;
- **Sleep:** indefinite microphone stop and motors disabled;
- **App off:** asynchronous clean app stop;
- **Pi shutdown:** explicit confirmation and graceful local power-off.

## Current verification status

Automated:

- Ruff passes.
- 23 pytest tests pass.
- Wheel builds successfully.
- No provider-key prefix is present in repository files.

Live bridge:

- OpenAI authentication succeeds.
- `gpt-realtime-2.1` is visible.
- Realtime `session.created` and `session.updated` succeed.
- Configured reasoning effort is reflected by the server.
- Native audio response and `ask_hermes` tool delegation succeed.
- ElevenLabs TTS and STT round trip succeeds.

Physical Reachy deployment:

- app installs and starts through the Reachy daemon;
- settings UI and model/voice discovery work;
- local camera capture returns a valid bounded JPEG without exposing it through the settings API;
- microphone frames stop and resume in the correct power modes;
- motor mode follows Standby/Awake/Meeting/Sleep;
- app-off exits cleanly and restarts successfully;
- daemon control loop reports zero errors;
- status endpoints pass repeated-request soak tests;
- network test reports zero packet loss.

Human acceptance still required per hardware/audio environment:

- real spoken **“Hey Hermes”** detection;
- natural Realtime turn-taking;
- acoustic barge-in while Reachy is speaking;
- microphone/speaker quality after physical enclosure or cooling changes.

## Performance observations

Reference deployment measurements:

| Path | Observed result |
|---|---:|
| Native Realtime short response | ~1.2 s |
| ElevenLabs TTS | ~0.6 s |
| ElevenLabs STT | ~1.1 s |
| Full Hermes pipeline agent request | ~14 s |
| Realtime request invoking `ask_hermes` | ~23 s in validation |

Hermes pipeline latency is dominated by per-request agent/context preparation rather than STT or TTS. Realtime provides the low-latency conversational path while retaining conditional Hermes delegation.

## Packaging and release scope

- Package and entry point: `reachy_mini_hermes` / `ReachyMiniHermes`.
- Reachy settings UI: port `8042`.
- Companion bridge: port `8643`, loopback by default.
- Apache-2.0 project license.
- Publishable through `reachy-mini-app-assistant publish` only after explicit user approval and Hugging Face authentication.
- Third-party KWS model is downloaded, checksum-verified, and retains its upstream notice.

## Deferred work

- Acoustic echo cancellation tuned for raw WebSocket Realtime audio.
- Camera frames and robot-specific Hermes tools.
- Local offline STT/TTS fallback on Reachy Wireless.
- Per-user wake-model personalization.
- Authenticated/TLS Reachy settings server for untrusted networks.
- Route-specific warm Hermes agent reuse with session serialization, cache signatures, lifecycle controls, and usage accounting.
- Broader simulated robot integration tests beyond the current unit and live hardware checks.
