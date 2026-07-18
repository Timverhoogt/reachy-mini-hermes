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
- Settings UI for conversation mode, speech providers, voices, interruption, local face/DOA controls, robot actions, power, app stop, and shutdown.
- Realtime client, silence playback asset, and tests for Realtime audio and power-state behavior.
- Optional on-demand Reachy camera tool with local diagnostics and Realtime image input.
- Authenticated, non-cacheable one-frame snapshot route for explicit image sharing.
- Privacy-controlled daemon-local face following that runs only during an active post-wake conversation.
- Optional wake-time DOA orientation using Reachy's local microphone-array direction estimate.
- Curated Realtime robot tools for look direction, authentic recorded emotions, and three recorded dance styles.
- Local Realtime power-mode tool for explicit Standby, Awake, timed Meeting, and Sleep commands.
- Native Reachy `goto_sleep()` transition before Sleep releases torque, with fail-safe torque retention if the movement fails.
- Serialized action worker that yields face/voice motion during explicit moves and cancels actions on Meeting/Sleep.
- Operations runbook covering deployment, rollback, cooling maintenance, health checks, and acoustic acceptance.

### Changed

- Reachy starts in Standby with motor torque disabled while local wake processing remains active.
- Provider secrets remain on the Hermes host; Reachy receives only a private bridge bearer token.
- Companion health output reports Realtime availability and model.
- Voice status exposes power mode, Meeting timer, provider state, and interruption count.
- Documentation now describes the dual-mode architecture and security boundaries.
- Hugging Face app page now presents the complete voice, interruption, camera, Hermes-tool, and power/privacy architecture.

### Fixed

- Updated OpenAI integration from the retired beta Realtime API shape to the GA protocol.
- Added the required Realtime output sample rate.
- App-off no longer waits on the daemon response from inside the process being stopped, removing a ten-second shutdown cycle and traceback.
- Realtime interruption tracks locally buffered audio after server generation finishes, immediately flushes Reachy playback, and truncates the unplayed OpenAI conversation audio.
- Camera access defaults to off and captures only one fresh JPEG per explicit Realtime visual-tool call.
- Camera capture waits for a completed tool item, deduplicates call IDs, and remains blocked in Meeting/Sleep.
- Awake now runs Reachy's physical wake-up motion instead of only enabling motor torque.
- Meeting and Sleep stop active playback and microphone capture before disabling motors.
- Meeting/Sleep action cancellation now restarts Reachy's playback backend when returning to Standby/Awake.
- Motion cancellation uses generation checks so dequeued actions cannot start after a privacy transition.
- Wake activation rechecks privacy before motors, face tracking, and cloud conversation startup.
- Pipeline synthesis and playback recheck privacy before starting TTS audio.
- `ask_hermes` now executes only for completed, deduplicated Realtime function-call items.
- Robot function-call output now reports the actual physical execution result instead of queue acceptance.
- DOA orientation accepts only a recent speech-validated, finite microphone-array estimate.
- Realtime barge-in now tracks only assistant audio-message item IDs, so function calls cannot make `conversation.item.truncate` target a non-audio item and terminate the session.
- A rejected truncation remains non-fatal after the local playback queue has already been cleared.

### Verified

- Ruff passes and 51 automated tests pass.
- Realtime session creation, audio response, configurable reasoning, and Hermes tool delegation succeed against the live API.
- ElevenLabs TTS/STT round trip succeeds.
- Reachy power states, clean app stop/restart, API soak tests, motor mode, and daemon health pass on the reference Reachy Mini Lite deployment.
