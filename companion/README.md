# Hermes Reachy voice bridge

Hermes Agent's public API server handles agent chat and tools. The companion bridge adds audio transcription and speech endpoints by reusing the **same Hermes profile's configured STT and TTS providers**. It also forwards chat to the official Hermes API server, so Reachy needs only one URL and bearer key.

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

## Run the bridge

Use the Python environment that belongs to Hermes Agent:

```bash
cd ~/.hermes/hermes-agent
venv/bin/python /path/to/reachy_mini_hermes/companion/hermes_reachy_bridge.py \
  --host 0.0.0.0 \
  --port 8643
```

The bridge reads `API_SERVER_KEY` from the active Hermes profile's environment,
`.env`, or `config.yaml` (the location used by `hermes config set`). For another profile:

```bash
venv/bin/python /path/to/hermes_reachy_bridge.py --profile my-profile --host 0.0.0.0
```

Then configure the Reachy app with:

```text
Bridge URL: http://<hermes-host-LAN-IP>:8643
API key:    the same API_SERVER_KEY
```

### Run at boot with systemd

The included example assumes this repository is cloned to
`~/reachy_mini_hermes`:

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
curl http://127.0.0.1:8643/health
```

## Security

- The default bind address is `127.0.0.1`.
- Bind to `0.0.0.0` only on a trusted LAN or VPN.
- Every chat/audio route uses constant-time bearer-token authentication.
- Provider keys stay on the Hermes host and are never sent to Reachy.
- For internet access, place the bridge behind HTTPS and an authenticated reverse proxy; do not expose the raw port publicly.
