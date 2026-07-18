# Security policy

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose a Hermes bearer token, provider credential, private audio, device-control path, or remote tool-execution path. Use GitHub Security Advisories for this repository.

Include the affected version, deployment topology, reproduction steps, exposed ports, and whether the bridge or Reachy settings server was reachable outside a trusted LAN/VPN.

## Credential model

Three credential classes must remain separate:

1. **`API_SERVER_KEY`** authenticates Reachy to the companion bridge and Hermes API Server. It can provide access to a tool-capable agent and must be treated as an administrative credential.
2. **Provider credentials**, such as `OPENAI_API_KEY` and `ELEVENLABS_API_KEY`, stay on the Hermes host. They must never be copied into Reachy's configuration.
3. **Reachy host privileges** control app lifecycle and Pi shutdown. The settings service must not be exposed to untrusted clients.

The Reachy configuration contains the bridge bearer token and is written with mode `0600`. The Hermes `.env` containing provider credentials should also use mode `0600`.

## Network exposure

- Keep Reachy and Hermes on a trusted LAN, management VLAN, or VPN.
- Do not expose ports `8042`, `8642`, or `8643` directly to the public internet.
- Port `8042` hosts the settings and power-control UI. Its confirmation strings prevent accidental clicks; they are not authentication.
- Port `8642` is the Hermes API Server.
- Port `8643` is the companion bridge, including the Realtime WebSocket proxy.
- Use TLS and an authenticated reverse proxy for any remote access.
- Restrict ingress with host/network firewalls to the Reachy and administrator addresses that actually need access.

## Realtime trust boundary

The OpenAI Realtime session receives post-wake audio and the configured system instructions. It does not receive the bridge bearer token or local provider keys.

The model can call `ask_hermes`. That tool forwards a request to the local Hermes API Server, where enabled Hermes tools determine the possible impact. Apply least privilege to the API Server platform toolset and disable unrelated MCP servers for the voice route where practical.

Do not claim that local wake-word processing makes the entire conversation local: after wake-up, pipeline STT/TTS or Realtime audio may be sent to configured cloud providers. When on-demand camera is enabled, a fresh frame may also be sent to the Realtime provider only after a visual tool call; continuous camera streaming is not used.

Optional face following is separate from cloud vision. It runs in the Reachy daemon only during an active post-wake conversation and is disabled when the conversation ends or Meeting/Sleep begins. Tracking frames are not forwarded to Hermes or OpenAI. Optional DOA reads one local microphone-array direction estimate after wake detection and uses it only to orient the head.

Realtime robot tools are allow-listed to local head direction, recorded emotion, and recorded dance actions. They cannot invoke shell commands, Home Assistant, files, or arbitrary Hermes tools. Those capabilities remain behind the authenticated `ask_hermes` boundary.

The local `set_reachy_power_mode` Realtime tool can select only Standby, Awake, Meeting, or Sleep after an explicit spoken request. It cannot stop the app, reboot, or shut down the Pi. Sleep stops voice capture immediately, runs Reachy's bounded native sleep movement, and releases torque only after the head is safely folded. A failed sleep movement keeps torque enabled rather than dropping the head. Sleep disables subsequent voice wake, so recovery requires the trusted settings UI or a physical control.

The snapshot API returns image bytes only after bearer-token authentication and explicit confirmation, and sets `Cache-Control: no-store`. The unauthenticated local camera test returns metadata only.

## Operational controls

- Keep `security.redact_secrets` enabled in Hermes.
- Rotate credentials after suspected disclosure or accidental posting in chat, logs, screenshots, or source control.
- Review systemd logs for tracebacks without copying secrets into support tickets.
- Leave Reachy in Standby, Meeting, or Sleep when motor torque is not required.
- Meeting and Sleep stop app microphone capture; stopping the app or powering off the Pi provides a stronger physical boundary.
- The shutdown endpoint requires confirmation and uses non-interactive local `sudo`. Scope the host's sudo policy as narrowly as practical.

## Repository hygiene

Before committing or publishing:

```bash
git diff --check
uv run ruff check .
uv run pytest
```

Also scan tracked and untracked text files for provider-key prefixes and bearer tokens. Never commit `.env`, Reachy's `config.json`, captured audio, or production logs.
