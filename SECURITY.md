# Security policy

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose a Hermes API key, provider credential, or remote tool execution path. Report it privately through GitHub Security Advisories for this repository.

Include the affected version, deployment topology, reproduction steps, and whether the bridge was exposed outside a trusted LAN/VPN.

## Deployment guidance

The Hermes API key grants access to an agent that may have terminal, smart-home, email, and other tools. Treat it like an administrative credential.

- Keep Reachy and Hermes on a trusted LAN or VPN.
- Do not expose ports `8642` or `8643` directly to the public internet.
- Use HTTPS and an authenticated reverse proxy for remote access.
- Rotate the API key after suspected disclosure.
- Keep `security.redact_secrets` enabled in Hermes.
- The bridge binds to loopback unless LAN binding is explicitly requested.
