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
- Tabbed settings UI with Dashboard, Announce, Robot, and Settings workspaces for clearer desktop, mobile, and Reachy Control use.
- Full announcement console with exact-text TTS, provider/model/voice overrides, quick templates, repeat/pause controls, a bounded serialized queue, independent Stop/clear, session-scoped browser draft preservation, and voice-only, wake-and-return, or stay-awake behavior.
- Manual semantic robot controls now include live Cartesian pose readout and bounded 1/2.5/5/10-unit precision steps for X/Y/Z translation, head roll/pitch/yaw, rotating-base yaw, and independent head/base/all centering, alongside confirmed motor/fold state, safe wake/fold power actions, nine-way head direction, curated expressions, dances, and cooperative movement cancellation.
- Priority Stop behavior, privacy revalidation at execution time, busy-request rejection, persistent action state, and mobile-safe controls prevent delayed or post-privacy motion. Precision motion uses app-owned 50 Hz interpolation so Stop/Meeting/Sleep can cancel both head and base movement; folding now waits for action-worker idle and re-verifies the physical sleep pose before torque release.
- Installable Android PWA metadata, branded icons, a root-scoped service worker, Dashboard install UX, and an HTTP Add-to-Home-Screen fallback.
- Realtime client, silence playback asset, and tests for Realtime audio and power-state behavior.
- Optional on-demand Reachy camera tool with local diagnostics and Realtime image input.
- Authenticated, non-cacheable one-frame snapshot route for explicit image sharing.
- Opt-in Robot-tab live viewer for the daemon's existing local WebRTC camera feed, with explicit Awake-only policy, muted audio, no public STUN dependency, and automatic disconnect on privacy/background transitions.
- Privacy-controlled daemon-local face following that runs only during an active post-wake conversation.
- Optional wake-time DOA orientation using Reachy's local microphone-array direction estimate.
- Curated Realtime robot tools for look direction, authentic recorded emotions, and three recorded dance styles.
- Local Realtime power-mode tool for explicit Standby, Awake, timed Meeting, and Sleep commands.
- Native Reachy `goto_sleep()` transition before Sleep releases torque, with fail-safe torque retention if the movement fails.
- Pose-aware safe folding before every Standby/Meeting/startup torque release, preventing a head drop when the app is restarted while Reachy is upright.
- Serialized action worker that yields face/voice motion during explicit moves and cancels actions on Meeting/Sleep.
- Operations runbook covering deployment, rollback, cooling maintenance, health checks, and acoustic acceptance.

### Changed

- Reachy starts in Standby with motor torque disabled while local wake processing remains active.
- Provider secrets remain on the Hermes host; Reachy receives only a private bridge bearer token.
- Companion health output reports Realtime availability and model.
- Voice status exposes power mode, Meeting timer, provider state, and interruption count.
- Documentation now describes the dual-mode architecture and security boundaries.
- The local camera viewer selects `ws://` on direct LAN HTTP and `wss://` on trusted HTTPS deployments, allowing Tailscale Serve to secure both the PWA and WebRTC signaling.
- The Robot tab now groups confirmed torque/fold state with Wake, safe Fold, and Stop controls; adds bounded diagonal looks, descriptive expression presets, dance-footprint labels, and clear-space confirmation for wide motion.
- Power and wake/fold transitions are serialized across clients; daemon, wake-motion, and torque-release failures now return explicit errors, keep the last confirmed motor state visible, and prevent false-success UI messages or post-release action execution.
- Hugging Face app page now presents the complete voice, interruption, camera, Hermes-tool, and power/privacy architecture.

### Fixed

- Updated OpenAI integration from the retired beta Realtime API shape to the GA protocol.
- Added the required Realtime output sample rate.
- App-off no longer waits on the daemon response from inside the process being stopped, removing a ten-second shutdown cycle and traceback.
- Realtime interruption tracks locally buffered audio after server generation finishes, immediately flushes Reachy playback, and truncates the unplayed OpenAI conversation audio.
- Camera access defaults to off and captures only one fresh JPEG per explicit Realtime visual-tool call.
- Camera capture waits for a completed tool item, deduplicates call IDs, and remains blocked in Meeting/Sleep; authenticated snapshots and local diagnostics now enforce and recheck the same privacy boundary while waiting for a frame.
- The local viewer closes both media sessions and signaling sockets, accepts only Reachy's named camera producer, and fails closed when runtime status disappears or the voice app stops.
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

- Ruff passes and 80 automated tests pass.
- Realtime session creation, audio response, configurable reasoning, and Hermes tool delegation succeed against the live API.
- ElevenLabs TTS/STT round trip succeeds.
- Reachy power states, clean app stop/restart, API soak tests, motor mode, and daemon health pass on the reference Reachy Mini Lite deployment.
