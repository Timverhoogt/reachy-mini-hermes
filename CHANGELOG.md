# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project currently remains in early alpha and has not published a stable compatibility promise.

## [Unreleased]

### Added

- Dual conversation modes: configurable Hermes pipeline and OpenAI `gpt-realtime-2.1` speech-to-speech.
- Authenticated GA Realtime WebSocket proxy on the companion bridge.
- Streaming PCM audio, semantic VAD, transcript events, selectable Marin/Cedar voices, and configurable reasoning effort.
- `ask_hermes` function delegation for persistent memory, current information, Home Assistant, files, and consequential actions.
- ElevenLabs Scribe and TTS provider/model selection with account voice discovery.
- Pipeline interruption by repeating **“Hey Hermes”** during playback.
- Realtime natural interruption and streamed-output flushing.
- Standby, Awake, timed Meeting, Sleep, app-off, and confirmed Pi shutdown controls.
- Motor torque and microphone lifecycle management for privacy/power states.
- Settings UI for conversation mode, speech providers, voices, interruption, power, app stop, and shutdown.
- Realtime client, silence playback asset, and tests for Realtime audio and power-state behavior.
- Optional on-demand Reachy camera tool with local diagnostics and Realtime image input.
- Authenticated, non-cacheable one-frame snapshot route for explicit image sharing.
- Operations runbook covering deployment, rollback, cooling maintenance, health checks, and acoustic acceptance.

### Changed

- Reachy starts in Standby with motor torque disabled while local wake processing remains active.
- Provider secrets remain on the Hermes host; Reachy receives only a private bridge bearer token.
- Companion health output reports Realtime availability and model.
- Voice status exposes power mode, Meeting timer, provider state, and interruption count.
- Documentation now describes the dual-mode architecture and security boundaries.

### Fixed

- Updated OpenAI integration from the retired beta Realtime API shape to the GA protocol.
- Added the required Realtime output sample rate.
- App-off no longer waits on the daemon response from inside the process being stopped, removing a ten-second shutdown cycle and traceback.
- Realtime interruption tracks locally buffered audio after server generation finishes, immediately flushes Reachy playback, and truncates the unplayed OpenAI conversation audio.
- Camera access defaults to off and captures only one fresh JPEG per explicit Realtime visual-tool call.
- Meeting and Sleep stop active playback and microphone capture before disabling motors.

### Verified

- Ruff passes and 23 automated tests pass.
- Realtime session creation, audio response, configurable reasoning, and Hermes tool delegation succeed against the live API.
- ElevenLabs TTS/STT round trip succeeds.
- Reachy power states, clean app stop/restart, API soak tests, motor mode, and daemon health pass on the reference Reachy Mini Lite deployment.
