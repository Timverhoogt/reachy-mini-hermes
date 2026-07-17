# Hermes Reachy companion bridge

The companion bridge gives Reachy one authenticated endpoint for:

- Hermes API Server chat;
- model and voice discovery;
- configured or explicitly selected STT/TTS providers;
- a private OpenAI Realtime WebSocket;
- `ask_hermes` delegation from Realtime back into the user's Hermes agent.
- pass-through of on-demand Reachy camera frames to Realtime image input.

Provider credentials stay on the Hermes host. Reachy stores only the bridge URL and the private `API_SERVER_KEY` bearer token.

## Prerequisites

```bash
hermes config set API_SERVER_ENABLED true
hermes config set API_SERVER_KEY 'use-a-long-random-secret'
hermes gateway restart
```

Verify Hermes itself:

```bash
curl http://127.0.0.1:8642/health
```

For Realtime mode, add a direct OpenAI project key to the active profile's `.env`:

```bash
OPENAI_API_KEY=your-openai-project-key
```

The OpenAI account must have API billing and access to `gpt-realtime-2.1`. A ChatGPT subscription alone is not an API credential.

## Run the bridge

Use the Python environment that belongs to Hermes Agent:

```bash
cd ~/.hermes/hermes-agent
venv/bin/python /path/to/reachy_mini_hermes/companion/hermes_reachy_bridge.py \
  --host 0.0.0.0 \
  --port 8643
```

The bridge resolves secrets from the selected Hermes profile's environment, `.env`, and configuration. For another profile:

```bash
venv/bin/python /path/to/hermes_reachy_bridge.py \
  --profile my-profile \
  --host 0.0.0.0
```

Configure Reachy with:

```text
Bridge URL: http://<hermes-host-LAN-IP>:8643
API key:    the same API_SERVER_KEY
```

## API surface

All `/v1/*` routes require:

```http
Authorization: Bearer <API_SERVER_KEY>
```

| Route | Purpose |
|---|---|
| `GET /health` | Hermes, provider, and Realtime availability |
| `GET /v1/models` | Reachy-compatible Hermes model routes |
| `GET /v1/voice-options` | STT/TTS models and account voices |
| `POST /v1/chat/completions` | Authenticated proxy to Hermes API Server |
| `POST /v1/audio/transcriptions` | Configured/local/ElevenLabs STT |
| `POST /v1/audio/speech` | Configured/Edge/ElevenLabs TTS |
| `GET /v1/realtime` | Authenticated WebSocket proxy to OpenAI Realtime |

The Realtime client sends an initial `session.start` envelope containing model, voice, reasoning effort, Hermes agent route, stable memory scope, system prompt, and the camera/robot-tool feature flags. The bridge then creates the OpenAI GA Realtime session and exposes `ask_hermes` plus only the enabled, curated Reachy-local tools.

### Realtime trust boundary

OpenAI may answer ordinary conversation directly. The session instructions require `ask_hermes` for:

- personal or persistent memory;
- current information;
- Home Assistant or other connected devices;
- local files and system state;
- consequential actions.

The bridge executes `ask_hermes` through the authenticated local Hermes API Server and returns the tool result to the Realtime session. OpenAI does not receive the Hermes bearer token, and Reachy does not receive the OpenAI key.

When Reachy enables camera support, the bridge advertises `capture_reachy_camera`. The tool call is forwarded to Reachy, which captures one bounded JPEG and sends it as an `input_image` conversation item. The bridge never polls or continuously streams the camera.

When Reachy enables robot tools, the bridge advertises `move_reachy_head`, `express_reachy_emotion`, and `dance_reachy`. The bridge never executes these physical actions itself: completed calls are forwarded to the robot, where an allow-listed local worker performs them. Knowledge, Home Assistant, files, memory, and consequential actions continue to route through `ask_hermes`.

## Run at boot with systemd

The included example assumes this repository is cloned to `~/reachy_mini_hermes`:

```bash
mkdir -p ~/.config/systemd/user
cp companion/hermes-reachy-bridge.service.example \
  ~/.config/systemd/user/hermes-reachy-bridge.service
systemctl --user daemon-reload
systemctl --user enable --now hermes-reachy-bridge.service
```

Verify with:

```bash
systemctl --user status hermes-reachy-bridge.service
curl -H "Authorization: Bearer $API_SERVER_KEY" \
  http://127.0.0.1:8643/health
```

Expected Realtime health fields:

```json
{
  "realtime_available": true,
  "realtime_model": "gpt-realtime-2.1"
}
```

## Logs

```bash
journalctl --user -u hermes-reachy-bridge.service -f
```

The bridge must not log bearer tokens, OpenAI keys, ElevenLabs keys, or response audio. Keep Hermes secret redaction enabled.

## Security

- The default bind address is `127.0.0.1`.
- Bind to `0.0.0.0` only on a trusted LAN or VPN.
- Every chat/audio/discovery/Realtime route uses constant-time bearer-token authentication.
- Provider keys remain on the Hermes host.
- The bearer token can invoke a tool-capable agent; treat it as an administrative credential.
- For remote access, use TLS and an authenticated reverse proxy. Never expose raw port `8643` publicly.
- Rotate both the bearer token and any provider credential after suspected disclosure.
