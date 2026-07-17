---
title: Reachy Mini Hermes
emoji: 🪽
colorFrom: indigo
colorTo: yellow
sdk: static
pinned: false
short_description: Talk to your own Hermes Agent through Reachy Mini
suggested_storage: medium
tags:
  - reachy_mini
  - reachy_mini_python_app
  - voice-assistant
  - hermes-agent
---

# Reachy Mini Hermes

Say **“Hey Hermes”** and talk to your own [Hermes Agent](https://github.com/NousResearch/hermes-agent) through Reachy Mini.

Wake-word detection stays local on the robot. After wake-up, choose between a configurable Hermes speech pipeline and low-latency OpenAI Realtime speech-to-speech. Provider credentials remain on the Hermes host.

> **Status:** early alpha. Automated, bridge, network, and physical power-state tests pass on Reachy Mini Lite. Every new robot/audio environment still needs a real spoken wake-word and acoustic barge-in acceptance test.

## Conversation modes

### OpenAI Realtime

```text
Reachy microphone
  → local “HEY HERMES” keyword spotting
  → authenticated private WebSocket bridge
  → OpenAI gpt-realtime-2.1 speech-to-speech
       ↳ ask_hermes tool when memory, current information,
         Home Assistant, files, or consequential actions are required
       ↳ capture_reachy_camera for a fresh on-demand image
  → streamed Reachy audio and motion
```

This is the recommended interactive mode. Ordinary conversation remains on the fast speech-to-speech path. The model delegates requests that need the user's Hermes identity, memory, tools, or devices to `ask_hermes` through the bridge.

### Hermes pipeline

```text
Reachy microphone
  → local “HEY HERMES” keyword spotting
  → adaptive utterance endpointing
  → configured STT provider
  → Hermes API Server agent, memory, and tools
  → configured TTS provider
  → Reachy speaker and motion
```

Pipeline mode supports selectable STT, TTS, agent model, voice, and continued conversation. It remains the fallback when Realtime API access is unavailable or a user wants explicit provider control.

## Features

- Local **Hey Hermes** wake phrase; cloud audio starts only after wake detection.
- Apache-2.0 open-vocabulary sherpa-onnx KWS model, downloaded and checksum-verified on first start.
- Dual conversation modes: configurable Hermes pipeline and `gpt-realtime-2.1`.
- Realtime semantic VAD, streaming audio, reasoning-effort selection, and natural interruption.
- Pipeline interruption by saying **“Hey Hermes”** while Reachy is speaking.
- `ask_hermes` tool delegation for memory, Home Assistant, current information, files, and actions.
- Privacy-preserving on-demand camera: one JPEG is captured only when a visual request needs it.
- Selectable ElevenLabs Scribe/TTS models and account voices without storing provider keys on Reachy.
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

> **Hey Hermes**

## Power and privacy states

| Mode | Microphone | Wake detection | Motor torque | Intended use |
|---|---|---|---|---|
| Standby | Local capture | Active | Disabled | Normal waiting state |
| Awake | Local capture | Active | Enabled | Keep Reachy physically awake |
| Meeting | Stopped | Disabled | Disabled | Timed privacy mode |
| Sleep | Stopped | Disabled | Disabled | Indefinite privacy mode |

The settings server stays available in these modes. **Stop voice app** exits the app and releases its resources. **Shut down Pi** requires typing `SHUTDOWN` in the UI before the host power-off command is scheduled.

Camera access is disabled by default. When enabled in Realtime mode, the model can request a single fresh frame for prompts such as “What do you see?” or “Look at this object.” Frames are not streamed continuously and the local camera test reports only JPEG metadata, not image content.

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
- Do **not** expose ports `8042`, `8642`, or `8643` directly to the internet.
- The settings UI includes power controls and therefore belongs only on a trusted management network.
- Hermes tools execute on the Hermes API-server host, not on Reachy.

Read [`SECURITY.md`](SECURITY.md) before exposing any endpoint beyond a trusted LAN/VPN.

## Performance notes

Observed on the reference deployment:

- Native Realtime audio response: approximately **1.2 seconds** for a short test response.
- ElevenLabs TTS: approximately **0.6 seconds**.
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

Current automated suite: **23 tests**.

The implementation plan and status are in [`plan.md`](plan.md). Changes are recorded in [`CHANGELOG.md`](CHANGELOG.md).

## Third-party model

The app downloads sherpa-onnx's GigaSpeech 3.3M open-vocabulary KWS model from its official GitHub release and verifies SHA-256 before extraction. Upstream model metadata declares Apache-2.0. See [`reachy_mini_hermes/assets/THIRD_PARTY_MODELS.md`](reachy_mini_hermes/assets/THIRD_PARTY_MODELS.md).

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
