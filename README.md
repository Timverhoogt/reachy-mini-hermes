---
title: Reachy Mini Hermes
emoji: 🪽
colorFrom: indigo
colorTo: yellow
sdk: static
pinned: false
short_description: Embodied Hermes voice, vision, tools, and supervised Kids Mode
suggested_storage: medium
tags:
  - reachy_mini
  - reachy_mini_python_app
  - voice-assistant
  - hermes-agent
  - openai-realtime
  - camera
  - home-assistant
  - kids-mode
  - elevenlabs
---

# Reachy Mini Hermes

Say **“Hey Hermes”**, **“Okay Nabu”**, or **“Hey Reachy”** and give Reachy Mini the voice, vision, memory, skills, and tools of your own [Hermes Agent](https://github.com/NousResearch/hermes-agent).

[**Open the app page**](https://huggingface.co/spaces/Timbo89/reachy_mini_hermes) · [**View the source**](https://github.com/Timverhoogt/reachy-mini-hermes)

Wake-word detection stays local on the robot. After wake-up, choose between a configurable Hermes speech pipeline and low-latency OpenAI Realtime speech-to-speech. Provider credentials remain on the Hermes host.

> **Status:** early alpha. Automated, bridge, network, camera, deployment, and physical power-state tests pass on Reachy Mini Lite. Every robot/audio environment still needs a real spoken wake-word and acoustic barge-in acceptance test.

## Conversation modes

### OpenAI Realtime

```text
Reachy microphone
  → local configured wake-phrase spotting
  → authenticated private WebSocket bridge
  → OpenAI gpt-realtime-2.1 speech-to-speech
       ↳ ask_hermes tool when memory, current information,
         Home Assistant, files, or consequential actions are required
       ↳ capture_reachy_camera for a fresh on-demand image
       ↳ local look, emotion, and authentic recorded-dance tools
  → streamed Reachy audio and motion
```

This is the recommended interactive mode. Ordinary conversation remains on the fast speech-to-speech path. The model delegates requests that need the user's Hermes identity, memory, tools, or devices to `ask_hermes` through the bridge.

### Hermes pipeline

```text
Reachy microphone
  → local configured wake-phrase spotting
  → adaptive utterance endpointing
  → configured STT provider
  → Hermes API Server agent, memory, and tools
  → configured TTS provider
  → Reachy speaker and motion
```

Pipeline mode supports selectable STT, TTS, agent model, voice, and continued conversation. It remains the fallback when Realtime API access is unavailable or a user wants explicit provider control.

## Features

- Local **Hey Hermes**, **Okay Nabu**, and **Hey Reachy** wake phrases; cloud audio starts only after local detection.
- Apache-2.0 open-vocabulary sherpa-onnx KWS model, downloaded and checksum-verified on first start.
- Dual conversation modes: configurable Hermes pipeline and `gpt-realtime-2.1`.
- Realtime semantic VAD, streaming audio, reasoning-effort selection, and natural interruption.
- Pipeline interruption by saying **“Hey Hermes”**, **“Okay Nabu”**, or **“Hey Reachy”** while Reachy is speaking.
- `ask_hermes` tool delegation for memory, Home Assistant, current information, files, and actions.
- Curated Realtime embodiment tools for looking, emotions, and authentic recorded Reachy dances.
- Optional daemon-local face following, active only after the wake phrase for the current conversation.
- Optional wake-time microphone-array direction finding so Reachy turns once toward the speaker locally.
- Privacy-preserving cameras: one JPEG is captured only when a visual request needs it, while an independent opt-in UI viewer connects directly to Reachy's local WebRTC feed.
- Selectable ElevenLabs Scribe/TTS models and account voices without storing provider keys on Reachy.
- Full announcement console with typed TTS, per-announcement provider/model/voice overrides, quick templates, repeat/pause controls, a bounded queue, independent Stop, and voice-only or safe wake/fold behavior.
- Supervised **Kids Mode** with five age-aware activities, English/Dutch profiles, 15–60 minute parent-selected sessions, automatic safe folding, optional gentle voice-state motion, and a dedicated moderated no-agent pipeline that removes camera, personal memory, files, messaging, devices, purchases, and power controls.
- Kids replies use ElevenLabs Flash v2.5 through a fixed private streaming endpoint; 24 kHz PCM is pushed to Reachy's speaker as chunks arrive, with the configured app voice retained as a failure fallback.
- Stable Hermes memory scope plus rotating conversation sessions after inactivity.
- Listening, processing, speaking, and error cues with optional voice-state motion.
- Standby, Awake, timed Meeting, Sleep, app-off, and confirmed Pi shutdown controls.
- Motor torque disabled in Standby, Meeting, and Sleep.
- Microphone capture stopped in Meeting and Sleep.
- Secrets stored with mode `0600`, masked in the UI, and excluded from logs.
- Reachy Mini App SDK lifecycle and app-store discovery.

## Requirements

- Reachy Mini SDK **1.9.0 or newer**.
- Python 3.11 or newer.
- A reachable Hermes Agent installation with the API Server enabled.
- Pipeline mode: configured STT and TTS providers.
- Realtime mode: an OpenAI API project key with access to `gpt-realtime-2.1`.
- Kids Mode: OpenAI moderation/chat access plus an `ELEVENLABS_API_KEY` for fixed-policy Flash v2.5 streaming TTS.
- Reachy and Hermes on a trusted LAN/VPN, or protected by TLS and an authenticated reverse proxy.

## 1. Prepare Hermes Agent

On the computer running Hermes:

```bash
hermes config set API_SERVER_ENABLED true
hermes config set API_SERVER_KEY 'replace-with-a-long-random-secret'
hermes gateway restart
```

`API_SERVER_KEY` is the private bearer token shared with Reachy. It is **not** an OpenAI key.

For Realtime mode, store the provider credential on the Hermes host:

```bash
hermes config env-path
# Add to the displayed .env file:
OPENAI_API_KEY=your-openai-project-key
chmod 600 ~/.hermes/.env
```

For pipeline mode, configure STT and TTS through Hermes or use the provider selectors exposed by the bridge:

```yaml
stt:
  enabled: true
  provider: local      # or groq/openai/mistral/etc.

tts:
  provider: edge       # or ElevenLabs/another configured provider
```

See the official [Hermes API Server documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server).

## 2. Run the companion bridge

Use Hermes' own Python environment so the bridge can reuse its configured providers:

```bash
cd ~/.hermes/hermes-agent
venv/bin/python /path/to/reachy_mini_hermes/companion/hermes_reachy_bridge.py \
  --host 0.0.0.0 \
  --port 8643
```

Verify locally:

```bash
curl -H "Authorization: Bearer $API_SERVER_KEY" \
  http://127.0.0.1:8643/health
```

A Realtime-ready response includes:

```json
{
  "status": "ok",
  "hermes_api": true,
  "realtime_available": true,
  "kids_chat_available": true,
  "kids_tts_streaming_available": true,
  "realtime_model": "gpt-realtime-2.1"
}
```

Read [`companion/README.md`](companion/README.md) for endpoints, profiles, service setup, and security notes.

## 3. Install the Reachy app

Development install:

```bash
uv pip install -e /path/to/reachy_mini_hermes
```

Wheel deployment:

```bash
uv build --wheel
uv pip install --reinstall --no-deps dist/reachy_mini_hermes-*.whl
```

Validate the public app structure when the Reachy app assistant is available:

```bash
reachy-mini-app-assistant check /path/to/reachy_mini_hermes
```

Start through the Reachy dashboard, or:

```bash
curl -X POST http://REACHY_HOST:8000/api/apps/start-app/reachy_mini_hermes
```

Open the settings page:

```text
http://REACHY_HOST:8042
```

Enter:

- **Bridge URL:** `http://HERMES_HOST:8643`
- **API key:** the same `API_SERVER_KEY` configured in Hermes
- **Conversation mode:** OpenAI Realtime or Hermes pipeline

Press **Test connection**, save, then say:

> **Hey Hermes**, **Okay Nabu**, or **Hey Reachy**

### Browser controls

The in-app UI is organized into five keyboard-accessible tabs:

- **Dashboard** — live state, power/privacy modes, app lifecycle, and the latest conversation.
- **Kids** — supervised, time-boxed child sessions with age/language profiles, five activities, optional gentle voice-state motion, automatic safe folding, and child-specific privacy/tool restrictions.
- **Announce** — exact-text TTS announcements with provider, voice, queue, repeat, and physical-behavior controls.
- **Robot** — safe manual look directions, 1/2.5/5/10 mm or degree precision controls for Cartesian head pose and rotating-base yaw, live pose readout, independent centering, curated emotions, three recorded dances, stop movement, and an opt-in local WebRTC camera viewer.
- **Settings** — Hermes bridge, voice, embodiment, privacy, and advanced timing configuration.

Remote motor controls expose the confirmed torque/fold state and keep **Wake & enable**, **Fold & disable**, and cooperative **Stop action** together at the top of the Robot tab. A nine-way bounded head pad adds diagonal looks. Precision controls can nudge X/Y/Z in 1, 2.5, 5, or 10 mm steps and roll/pitch/yaw or rotating-base yaw by the same number of degrees; conservative Cartesian limits and measured pose readback remain visible beside the controls. Head, base, or both can be centered independently. Expression presets describe their motion character; dance presets label compact, medium, and wide movement, with an extra clear-space confirmation for the wide Energetic move. Controls use the same serialized semantic action worker as Realtime—the browser cannot submit raw joints, arbitrary move names, shell commands, or motor calibration. A manual movement from Standby first completes Reachy's native wake motion and leaves it Awake. Power transitions pause all presets, additional taps are rejected while an action is active, privacy is rechecked immediately before execution, and Meeting/Sleep block movement. **Stop action** remains available during semantic movement, cancels active plus queued work without changing power mode or initiating a new pose, and preserves the voice playback pipeline. Safe folding is never interrupted. The UI is intended only for a trusted LAN/VPN.

Kids Mode is deliberately separate from the normal Hermes agent session. A parent selects an optional nickname, age band (4–6, 7–9, or 10–12), English or Dutch, a 15/30/45/60-minute limit, and one of Buddy chat, Story maker, Quiz quest, Riddle box, or Calm corner. Set the initial PIN from a trusted parent device before giving a child access because the first caller can claim an unset PIN. Parent management is protected by a 6–8 digit PIN stored only as a salted `scrypt` verifier; the plaintext PIN is not persisted in the browser, public status, logs, or configuration responses. Starting the mode opens a fresh, bounded child pipeline through the private `/v1/kids/chat` route, with pre/post moderation and no normal Hermes memory or tool session. Camera, agent/delegation tools, files, messaging, Home Assistant, purchases, power tools, and explicit robot actions remain unavailable. Normalized, approved complete responses receive separate short-lived, single-use bridge capabilities for streaming and configured-TTS fallback, each bound to the child session and exact text, then stream through fixed-policy ElevenLabs Flash v2.5 as 24 kHz PCM—unmoderated model tokens are never sent directly to speech. Optional motion is limited to gentle local listening/thinking/speaking cues. Starting Kids Mode closes any prior conversation; ending it immediately invalidates the child session; synchronous STT/chat HTTP calls are not transport-aborted and may run until their bounded timeout, but their returned output is discarded. Ending also interrupts active streaming TTS and audio playback, cancels movement, clears queued speech, and runs the verified safe fold before torque release. A monotonic server timer enforces the limit and gives a five-minute warning. This is a supervised beta feature, not a babysitter, therapist, medical service, or emergency service; generative replies can still be wrong. Child audio goes to the configured STT provider, moderated child text goes to OpenAI, and approved reply text goes to ElevenLabs; the optional nickname is included in the deterministic ElevenLabs greeting. Exclusion from Hermes memory is not a provider-retention guarantee—review each provider's data controls before use.

### Bluetooth controllers

> **Hardware scope:** Bluetooth controller management is supported only on **Reachy Mini Wireless**, using its Raspberry Pi Bluetooth radio. It is not supported on Reachy Mini Lite or wired-only installations.

The Robot tab can pair and manage PlayStation-compatible or other Linux gamepads through Reachy Pi's BlueZ adapter. Put a DualShock 4 into pairing mode with **Share + PS**, or a DualSense with **Create + PS**, then use **Scan**, **Pair & connect**, and **Enable controller movement**. Controller movement is opt-in and uses the same allow-listed action queue as the browser:

- Left stick or D-pad: bounded eight-way head look.
- Cross: center the head.
- Square: Happy expression.
- Triangle: Surprised expression.
- Circle: cooperatively stop the active and queued robot action.

Kids Mode, Meeting, Sleep, privacy mode, motor transitions, and the existing robot-action queue remain authoritative. The controller cannot submit raw joints, calibration, shell commands, power changes, dances, camera requests, agent tools, or Home Assistant actions.

Reachy Pi needs BlueZ and Linux joystick support. For a manual deployment:

```bash
sudo apt-get install bluez joystick
sudo systemctl enable --now bluetooth
sudo usermod -aG input "$USER"
# Log out/reboot after changing groups, then verify:
bluetoothctl show
ls -l /dev/input/js*
```

If the Reachy app daemon runs as a dedicated service account, add that account—not only the interactive SSH account—to `input`, verify that `bluetoothctl show` works under that account through the image's BlueZ D-Bus/polkit policy, then restart the daemon. A `bluetooth` Unix group is not portable and is not assumed. Do not grant blanket passwordless sudo to the web app; pairing uses bounded `bluetoothctl` arguments and strict MAC-address validation.

The Dashboard is also a Progressive Web App. Android Chrome exposes an **Install app** button when the page is served from a trusted HTTPS origin; the manifest, standalone display mode, icons, root-scoped service worker, and Android shortcuts are bundled with the app. The service worker caches only the static application shell and never intercepts `/api/` requests. On the direct `http://<reachy-address>:8042` LAN URL, use Chrome's **⋮ → Add to Home screen** fallback; robot controls still require a live connection to Reachy. For private trusted HTTPS, install Tailscale on Reachy and expose the UI with `tailscale serve --bg --https=443 http://127.0.0.1:8042`. Expose camera signaling separately with `tailscale serve --bg --tls-terminated-tcp=8443 tcp://127.0.0.1:8443`; the viewer automatically selects `wss://` from an HTTPS page and retains `ws://` on direct LAN HTTP. Use **Serve**, never Funnel, so the dashboard remains tailnet-only.

## Power and privacy states

| Mode | Microphone | Wake detection | Local face tracking | Motor torque | Intended use |
|---|---|---|---|---|---|
| Standby | Local capture | Active | Off until an active conversation | Disabled | Normal waiting state |
| Awake | Local capture | Active | Optional during active conversation | Enabled | Keep Reachy physically awake |
| Meeting | Stopped | Disabled | Disabled | Disabled | Timed privacy mode |
| Sleep | Stopped | Disabled | Disabled | Disabled | Indefinite privacy mode |

The settings server stays available in these modes. **Stop voice app** exits the app and releases its resources. **Shut down Pi** requires typing `SHUTDOWN` in the UI before the host power-off command is scheduled.

Camera access is disabled by default and split into two independent controls. **On-demand camera** allows Realtime to request one fresh frame for prompts such as “What do you see?”; it never creates a continuous cloud stream. **Local live camera** enables an explicit Start button in the Robot tab. That viewer uses the same daemon-local WebRTC producer as Reachy Mini Control, disables the remote audio track, uses no public STUN service, and sends video directly from Reachy to the current browser—not through Hermes or OpenAI. It is available only while Reachy is Awake and disconnects when the Robot tab is left, the page is backgrounded, or power enters Standby, Meeting, or Sleep. The local camera test still reports only JPEG metadata, not image content.

Local face following is a separate opt-in. It uses Reachy SDK 1.9 daemon-side tracking only after a configured local wake phrase and stops when that conversation ends or Meeting/Sleep begins. Tracking frames are not forwarded to Hermes or OpenAI. Optional DOA uses the microphone array's local angle estimate once after wake detection, then discards it after orienting the head.

Realtime mode also exposes the local `set_reachy_power_mode` tool. Explicit commands such as **“go to Standby,” “stay Awake,” “Meeting mode for 45 minutes,”** and **“go to Sleep”** change the real microphone, wake-detector, tracking, motion, and motor state. Meeting defaults to 30 minutes when no duration is given. Standby, Meeting, and Sleep end the current conversation immediately. Every transition that releases motor torque—including startup into Standby—first checks the physical head pose and runs Reachy's native `goto_sleep()` movement when needed, so the head folds gently into the body before torque is released. If the movement fails, torque stays enabled rather than allowing the head to drop. Sleep cannot be exited by voice because its microphone and wake detector are off; use the trusted settings UI or a physical control. App-off and Pi shutdown are intentionally not voice tools.

Reachy SDK downloads its small YuNet detector on first use. If the daemon reports a Hugging Face `401` while fetching `pollen-robotics/face_detection_yunet_2026may`, prefetch that public model into the Pi user's Hugging Face cache (or copy an authenticated workstation cache snapshot there) and retry. The model then runs locally; no Hugging Face credential needs to remain on Reachy.

Realtime physical tools are local and allow-listed: `move_reachy_head`, `express_reachy_emotion`, and `dance_reachy`. They run in a serialized motion worker so microphone streaming remains responsive. Recorded-move audio is suppressed because Hermes remains the only voice source.

An authenticated `POST /api/camera/snapshot` route can return one current JPEG for explicitly requested sharing or diagnostics. It requires the private bridge bearer token, the `camera` confirmation value, and disables response caching.

## Configuration storage

Default path:

```text
~/.local/share/reachy_mini_hermes/config.json
```

Managed-installation overrides:

```bash
REACHY_MINI_HERMES_CONFIG=/path/to/config.json
REACHY_MINI_HERMES_MODEL_DIR=/path/to/model-cache
```

The configuration file is written with permissions `0600`. It contains the bridge bearer token, but no OpenAI, ElevenLabs, or other provider credential.

## Operational checks

See [`OPERATIONS.md`](OPERATIONS.md) for deployment, health checks, logs, rollback, thermal checks, and the post-maintenance acceptance checklist.

A minimal check is:

```bash
curl http://REACHY_HOST:8000/api/apps/current-app-status
curl http://REACHY_HOST:8042/api/status
curl -H "Authorization: Bearer $API_SERVER_KEY" http://HERMES_HOST:8643/health
```

## Security

- The bridge defaults to `127.0.0.1`; LAN binding is explicit.
- Chat, audio, discovery, and Realtime routes require constant-time bearer-token authentication.
- Provider credentials never leave the Hermes host.
- Camera frames leave Reachy only after local wake detection, during an active Realtime session, and after the model requests visual grounding.
- Face-tracking frames remain local to Reachy's daemon and tracking stops outside the active conversation or in Meeting/Sleep.
- Realtime physical tools are curated local motions; privileged and consequential actions still go through `ask_hermes`.
- Do **not** expose ports `8042`, `8642`, or `8643` directly to the internet.
- The settings UI includes power controls and therefore belongs only on a trusted management network.
- Hermes tools execute on the Hermes API-server host, not on Reachy.

Read [`SECURITY.md`](SECURITY.md) before exposing any endpoint beyond a trusted LAN/VPN.

## Performance notes

Observed on the reference deployment:

- Native Realtime audio response: approximately **1.2 seconds** for a short test response.
- ElevenLabs TTS: approximately **0.6 seconds**.
- Kids ElevenLabs Flash streaming: approximately **0.375 seconds to the first 24 kHz PCM chunk** on the reference network.
- ElevenLabs STT: approximately **1.1 seconds**.
- Full Hermes pipeline request: approximately **14 seconds** due primarily to per-request Hermes context preparation.
- A Realtime request that invokes `ask_hermes` inherits that Hermes agent latency.

These are deployment observations, not service-level guarantees.

## Development

```bash
uv sync --group dev
uv run ruff check .
uv run pytest
uv build --wheel
reachy-mini-app-assistant check .
```

Current automated suite: **143 tests**.

The implementation plan and status are in [`plan.md`](plan.md). Changes are recorded in [`CHANGELOG.md`](CHANGELOG.md).

## Third-party model

The app downloads sherpa-onnx's GigaSpeech 3.3M open-vocabulary KWS model from its official GitHub release and verifies SHA-256 before extraction. Upstream model metadata declares Apache-2.0. See [`reachy_mini_hermes/assets/THIRD_PARTY_MODELS.md`](reachy_mini_hermes/assets/THIRD_PARTY_MODELS.md).

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
