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

The robot handles local wake detection, speech capture, clear listening/processing cues, and expressive voice-state motion. Hermes Agent remains the brain: it keeps its configured model, memories, skills, MCP servers, and tools.

> **Status:** early alpha. The software has unit/integration coverage, but every new robot/audio environment still needs wake-threshold and VAD tuning.

## Architecture

```text
Reachy microphone
  → local open-vocabulary “HEY HERMES” keyword spotting
  → adaptive utterance endpointing
  → Hermes Reachy voice bridge (configured Hermes STT)
  → Hermes API Server (agent + tools + session memory)
  → Hermes Reachy voice bridge (configured Hermes TTS)
  → Reachy speaker and motion
```

The companion bridge is intentionally small. Hermes' standard API server already provides agent chat, sessions, memory, skills, and tools; the bridge only adds authenticated audio transcription and speech endpoints using the same Hermes profile's STT/TTS configuration.

## Features

- Local **Hey Hermes** wake phrase without training a proprietary wake model.
- Apache-2.0 open-vocabulary sherpa-onnx KWS model, downloaded and checksum-verified on first start.
- Configurable Hermes bridge URL and bearer key through Reachy's app settings UI.
- Secrets are stored with mode `0600`, masked in the UI, and never logged.
- Stable `X-Hermes-Session-Key` for long-term memory plus rotating conversation sessions after inactivity.
- Listening, processing, speaking, and error earcons/poses.
- Optional continued conversation without repeating the wake phrase.
- Supports the Reachy Mini App SDK lifecycle and app-store discovery.

## How this differs from the existing community `hermes_mini` app

A separate community app named `hermes_mini` was already available when this
project was created. That app uses always-on voice activity detection and
robot-side cloud STT/TTS credentials. Reachy Mini Hermes takes a different
approach:

- local **“Hey Hermes”** activation instead of continuous listening;
- Hermes-hosted STT/TTS through the companion bridge;
- no OpenAI, Groq, ElevenLabs, or other provider keys stored on Reachy;
- explicit listening and command-captured feedback;
- one authenticated URL for agent chat, transcription, and speech.

Both are independent open-source integrations; users can choose the interaction
and deployment model that suits them.

## Requirements

- Reachy Mini SDK **1.9.0 or newer**.
- Python 3.11 or newer.
- A reachable Hermes Agent installation with:
  - API Server enabled;
  - STT enabled/configured;
  - TTS configured.
- Reachy and Hermes on a trusted LAN/VPN, or an HTTPS reverse proxy.

## 1. Prepare Hermes Agent

On the computer running Hermes:

```bash
hermes config set API_SERVER_ENABLED true
hermes config set API_SERVER_KEY 'replace-with-a-long-random-secret'
hermes gateway restart
```

Configure voice providers if needed:

```bash
hermes config edit
```

Relevant sections:

```yaml
stt:
  enabled: true
  provider: local      # or groq/openai/mistral/etc.

tts:
  provider: edge       # free default; other Hermes providers also work
```

See the official [Hermes API Server documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server).

## 2. Run the voice bridge

Use Hermes' own Python environment so the bridge can reuse its configured STT/TTS implementation:

```bash
cd ~/.hermes/hermes-agent
venv/bin/python /path/to/reachy_mini_hermes/companion/hermes_reachy_bridge.py \
  --host 0.0.0.0 \
  --port 8643
```

Verify from the Reachy machine:

```bash
curl http://HERMES_HOST:8643/health
```

Read [`companion/README.md`](companion/README.md) for profile, security, and service setup notes.

## 3. Install the Reachy app

Development install:

```bash
uv pip install -e /path/to/reachy_mini_hermes
```

Validate the public app structure:

```bash
reachy-mini-app-assistant check /path/to/reachy_mini_hermes
```

Start it through the Reachy dashboard, or:

```bash
curl -X POST http://REACHY_HOST:8000/api/apps/start-app/reachy_mini_hermes
```

Open the settings page from the dashboard, or visit:

```text
http://REACHY_HOST:8042
```

Enter:

- **Bridge URL:** `http://HERMES_HOST:8643`
- **API key:** the same `API_SERVER_KEY` configured in Hermes

Press **Test connection**, save, then say:

> **Hey Hermes**

Wait for the rising listening chime, speak your request, and stop when finished. A descending processing chime confirms that the command was captured.

## Configuration storage

By default:

```text
~/.local/share/reachy_mini_hermes/config.json
```

Override for managed installations:

```bash
REACHY_MINI_HERMES_CONFIG=/path/to/config.json
REACHY_MINI_HERMES_MODEL_DIR=/path/to/model-cache
```

The configuration file is written with permissions `0600`.

## Security

- The bridge defaults to `127.0.0.1`; LAN binding is explicit.
- Chat, STT, and TTS endpoints require constant-time bearer-token authentication.
- Provider credentials never leave the Hermes host.
- Do **not** expose port 8643 directly to the internet. Use HTTPS, a VPN, or an authenticated reverse proxy.
- Hermes tools execute on the Hermes API-server host, not on Reachy.

## Development

```bash
uv sync --group dev
uv run ruff check .
uv run pytest
uv build
reachy-mini-app-assistant check .
```

The implementation plan and current scope are in [`plan.md`](plan.md).

## Third-party model

The app downloads sherpa-onnx's GigaSpeech 3.3M open-vocabulary KWS model from its official GitHub release and verifies SHA-256 before extraction. Upstream model metadata declares Apache-2.0. See [`reachy_mini_hermes/assets/THIRD_PARTY_MODELS.md`](reachy_mini_hermes/assets/THIRD_PARTY_MODELS.md).

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
