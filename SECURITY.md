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
- Do not expose ports `8042`, `8443`, `8642`, or `8643` directly to the public internet.
- Port `8042` hosts the settings and power-control UI. Its confirmation strings prevent accidental clicks; they are not authentication.
- Port `8443` hosts the daemon's WebRTC signaling service for the local camera producer.
- Port `8642` is the Hermes API Server.
- Port `8643` is the companion bridge, including the Realtime WebSocket proxy.
- Use TLS and an authenticated reverse proxy for any remote access. Tailscale Serve can terminate HTTPS for port `8042` and TLS-terminated TCP/WSS for port `8443`; use Serve rather than public Funnel and restrict access with tailnet grants/ACLs.
- Restrict ingress with host/network firewalls to the Reachy and administrator addresses that actually need access.

## Realtime trust boundary

The OpenAI Realtime session receives post-wake audio and the configured system instructions. It does not receive the bridge bearer token or local provider keys.

The model can call `ask_hermes`. That tool forwards a request to the local Hermes API Server, where enabled Hermes tools determine the possible impact. Apply least privilege to the API Server platform toolset and disable unrelated MCP servers for the voice route where practical.

## Kids Mode trust boundary

Kids Mode does not use the normal Hermes agent or Realtime tool session. A fresh random child session is routed through authenticated `/v1/kids/chat` requests with bounded input/output. The bridge accepts only fixed age-band/activity/language enums, constructs the full child policy itself, and owns a capped two-hour in-memory history; no caller-supplied system prompt or history is accepted. Camera, agent/delegation, power, Home Assistant, messaging, files, purchases, and explicit robot-action tools are not advertised or accepted. Both child input and complete model output are moderated before approved text can reach speech.

Child speech uses authenticated Kids-only `/v1/kids/speech/stream` and `/v1/kids/speech/fallback` routes. The streaming route fixes ElevenLabs Flash v2.5, the configured child voice, and 24 kHz PCM; fallback uses the caregiver-configured host TTS only after streaming fails. Complete normalized, post-moderated text receives separate short-lived, single-use bridge capabilities for streaming and configured-TTS fallback, each tied to the exact session and text digest; both speech paths reject missing, expired, altered, or replayed approvals. Raw or unmoderated LLM tokens are never streamed directly to the speaker. Parent stop, privacy, timeout, and app shutdown clear queued audio and interrupt network streaming.

Parent management requires a 6–8 digit numeric PIN. Five failed checks trigger a five-minute server-side lockout.

The initial PIN setup is a trusted-management operation: the first caller can claim an unset PIN. Configure it from a caregiver-controlled device before exposing the dashboard to a child. Kids Mode sends child audio to the configured STT provider, moderated child text to OpenAI, and approved response text to ElevenLabs; an optional nickname is also included in the deterministic ElevenLabs greeting. Exclusion from Hermes memory is not a provider-retention guarantee. Review and configure each provider's data controls before use. Only a salted `scrypt` verifier is stored; plaintext PINs must not enter browser storage, public status, logs, or public configuration responses. Public transcript, response preview, nickname, and internal child-session identifiers stay redacted while the child lock is active. The server uses a monotonic authoritative deadline, and stopping a child session does not automatically unlock parent management.

Do not claim that local wake-word processing makes the entire conversation local: after wake-up, pipeline STT/TTS or Realtime audio may be sent to configured cloud providers. When on-demand camera is enabled, a fresh frame may also be sent to the Realtime provider only after a visual tool call; continuous camera streaming is not used.

Optional face following is separate from cloud vision. It runs in the Reachy daemon only during an active post-wake conversation and is disabled when the conversation ends or Meeting/Sleep begins. Tracking frames are not forwarded to Hermes or OpenAI. Optional DOA reads one local microphone-array direction estimate after wake detection and uses it only to orient the head.

Realtime and browser robot controls are allow-listed to local head direction, recorded emotion, and recorded dance actions. The UI and model cannot submit raw joints, arbitrary move names, shell commands, Home Assistant, files, or arbitrary Hermes tools. The Robot tab's motor-state indicator reports only the runtime's last confirmed daemon torque command and safe-fold flag; the browser never queries encoders or writes motor configuration directly. Browser actions pass through the same bounded worker, movement arbitration, power-mode checks, and cancellation generation as Realtime actions; manual requests reject additional queueing while busy, and the worker rechecks privacy immediately before physical execution. They are rejected in Meeting/Sleep and automatically complete the native wake transition when invoked from Standby. **Stop action** is an out-of-band priority action for semantic moves: it cooperatively cancels active and queued moves without changing power mode, initiating a pose, interrupting safe folding, or stopping the voice playback pipeline. Agent capabilities remain behind the authenticated `ask_hermes` boundary.

The local `set_reachy_power_mode` Realtime tool can select only Standby, Awake, Meeting, or Sleep after an explicit spoken request. It cannot stop the app, reboot, or shut down the Pi. Before any Standby, Meeting, Sleep, or startup transition releases torque, the runtime verifies the current pose and runs Reachy's bounded native sleep movement when the head is not already folded. A failed movement keeps torque enabled rather than dropping the head. Sleep disables subsequent voice wake, so recovery requires the trusted settings UI or a physical control.

The snapshot API returns image bytes only after bearer-token authentication and explicit confirmation, and sets `Cache-Control: no-store`. The unauthenticated local camera test returns metadata only.

The optional local live viewer does not create a Hermes camera endpoint. After an explicit user action while Reachy is Awake, the browser connects directly to the daemon's existing GStreamer WebRTC producer on port 8443—the same feed used by Reachy Mini Control. Direct LAN HTTP uses `ws://`; a trusted HTTPS deployment uses `wss://` with TLS terminated by its private reverse proxy. The UI disables the audio track, adds no public STUN service, and closes its session on tab exit, page backgrounding, Standby, Meeting, Sleep, Hermes status loss, or app shutdown. The `camera_feed_enabled` setting controls this UI, but it does not disable Reachy's upstream daemon producer or prevent another authorized Reachy Control client from connecting; network access to the daemon and signaling port remains the real trust boundary.

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
