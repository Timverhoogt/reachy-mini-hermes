# Operations runbook

This runbook covers private Reachy Mini deployments using the Reachy daemon, a Hermes host, and the companion bridge.

## Service map

| Component | Default location | Port/service |
|---|---|---|
| Reachy daemon | Reachy/Pi | `8000`, `reachy-mini-daemon.service` |
| Reachy Hermes settings | Reachy app process | `8042` |
| Hermes API Server | Hermes host | `8642`, Hermes gateway |
| Reachy companion bridge | Hermes host | `8643`, `hermes-reachy-bridge.service` |

## Pre-deployment checks

On the development/Hermes host:

```bash
uv run ruff check .
uv run pytest
uv build --wheel
```

Confirm no production configuration, `.env`, captured audio, or provider key is included in the wheel or Git diff.

On Reachy:

```bash
systemctl is-active reachy-mini-daemon.service
curl -fsS http://127.0.0.1:8000/api/daemon/status
```

Stop any running app before replacing its installed package:

```bash
curl -X POST http://REACHY_HOST:8000/api/apps/stop-current-app
```

## Wheel deployment

Copy the wheel to Reachy, then install it into the same Python environment used by the Reachy daemon:

```bash
scp dist/reachy_mini_hermes-*.whl REACHY_HOST:/tmp/

uv pip install \
  --python /path/to/reachy_mini/.venv/bin/python \
  --reinstall \
  --no-deps \
  /tmp/reachy_mini_hermes-*.whl
```

`--no-deps` is appropriate only after verifying that the Reachy environment already satisfies the package requirements. In particular, Realtime mode needs `websockets>=15,<17`.

Start the app:

```bash
curl -X POST http://REACHY_HOST:8000/api/apps/start-app/reachy_mini_hermes
```

Wait for both conditions:

```bash
curl -fsS http://REACHY_HOST:8000/api/apps/current-app-status
curl -fsS http://REACHY_HOST:8042/api/status
```

The app manager may report `running` several seconds before the settings server and media pipeline are ready.

## Companion bridge deployment

The bridge normally runs directly from the checked-out repository. After bridge code or Hermes-host credentials change:

```bash
systemctl --user restart hermes-reachy-bridge.service
systemctl --user status hermes-reachy-bridge.service
```

Validate with the private bearer token:

```bash
curl -H "Authorization: Bearer $API_SERVER_KEY" \
  http://127.0.0.1:8643/health
```

For Realtime mode, require:

- `hermes_api: true`;
- `realtime_available: true`;
- `realtime_model: gpt-realtime-2.1`.

For Kids Mode, additionally require:

- `kids_chat_available: true`;
- `kids_ispy_available: true` before offering the I Spy activity;
- `kids_tts_streaming_available: true`;
- successful authenticated `/v1/kids/chat` moderation/chat and `/v1/kids/speech/stream` PCM probes.

For an I Spy release, additionally verify with an adult supervising the robot: explicit consent before capture; the visible camera-search indicator; exactly five bounded retained viewpoints at `0° / −60° / −120° / +60° / +120°`; no commanded base segment larger than 60°; camera revocation before the neutral return and guesses; English and Dutch clue/hint/reveal flows; reveal no later than the sixth guess; Stop during both search and provider wait; bridge target deletion; safe neutral/fold; and no retained frame bytes. Never run this physical acceptance unattended.

For Agent Mode, verify the authenticated manifest and keep every allowlist explicit:

```bash
curl -fsS -H "Authorization: Bearer ***" \
  http://127.0.0.1:8643/v1/agent/capabilities
```

Confirm that the manifest contains no T4, shell, arbitrary-file, or maintenance capability. Test one configured read, one reversible Home Assistant action plus undo, one timer callback, and one draft whose exact phone approval executes once and rejects replay/edits. Keep Home Assistant/provider credentials on the Hermes host. `REACHY_AGENT_REMINDER_CALLBACK_URL` must target Reachy's private settings endpoint and its callback token must match Reachy's bridge bearer. Stop Agent and starting Kids Mode must invalidate in-flight work and pending approvals before speech; `/v1/agent/activity` remains metadata-only.

## Health checks

### Reachy app

```bash
curl -fsS http://REACHY_HOST:8042/api/status
```

Important fields:

- `runtime.state`;
- `runtime.power_mode`;
- `runtime.last_error`;
- `runtime.audio_frames_processed`;
- `runtime.turns_completed`;
- `runtime.interruptions`;
- `config.conversation_mode`;
- `config.camera_enabled`, `runtime.camera_captures`, and `runtime.camera_last_error`.

Test one local camera frame without returning its image content:

```bash
curl -X POST http://REACHY_HOST:8042/api/camera/test \
  -H 'Content-Type: application/json' \
  -d '{"confirm":"camera"}'
```

In Standby, `audio_frames_processed` should increase while daemon motor mode remains `disabled`. In Meeting or Sleep, the frame count should stop increasing.

### Reachy daemon

```bash
curl -fsS http://REACHY_HOST:8000/api/daemon/status
systemctl show reachy-mini-daemon.service -p ActiveState -p NRestarts
```

Check:

- daemon state is `running`;
- motor mode matches the requested app power state;
- control-loop error count remains zero;
- restart count does not increase unexpectedly.

### Logs

```bash
journalctl -u reachy-mini-daemon.service -f
journalctl --user -u hermes-reachy-bridge.service -f
```

Expected startup milestones include:

- app process started;
- settings server listening on `8042`;
- Reachy Hermes audio input/output rates logged;
- motors disabled when entering Standby.

Treat tracebacks, `Reachy voice runtime failed`, repeated WebSocket closures, and increasing daemon restarts as failures. Hardware GPU-device discovery warnings from ONNX Runtime may be harmless on a Pi when CPU inference continues successfully.

## Bluetooth and controller checks

This procedure applies only to **Reachy Mini Wireless**. Reachy Mini Lite and wired-only installations do not expose this Bluetooth controller feature as supported hardware.

1. Verify the adapter and BlueZ service:

   ```bash
   systemctl is-active bluetooth
   bluetoothctl show
   sudo rfkill list bluetooth
   ```

2. Verify the Reachy app service account belongs to `input` and can run `bluetoothctl show` through the target image's BlueZ D-Bus/polkit policy. Do not assume a `bluetooth` Unix group exists. Restart the Reachy daemon after changing groups.
3. Put the controller in pairing mode and use **Robot → Bluetooth gamepad → Scan**. Confirm Pair, Trust, and Connect all succeed.
4. Confirm the kernel created `/dev/input/js0` (or another `js*` device) and the app reports **Controller ready**.
5. With clear space and controller movement enabled, test one input at a time: D-pad look, Cross center, Square Happy, Triangle Surprised, Circle Stop.
6. Enter Meeting, Sleep, and Kids Mode and confirm movement inputs are rejected. Disconnect the controller and confirm the UI returns to Waiting/Disconnected without moving Reachy.
7. Do not map power, shutdown, dance, camera, agent, smart-home, or raw joint operations to the controller.

Useful diagnostics:

```bash
bluetoothctl devices Paired
bluetoothctl devices Connected
jstest /dev/input/js0
journalctl -u bluetooth -n 100 --no-pager
```

## Power controls

Use the settings UI where practical. API equivalents:

```bash
curl -X POST http://REACHY_HOST:8042/api/power \
  -H 'Content-Type: application/json' \
  -d '{"mode":"standby"}'

curl -X POST http://REACHY_HOST:8042/api/power \
  -H 'Content-Type: application/json' \
  -d '{"mode":"meeting","duration_minutes":60}'

curl -X POST http://REACHY_HOST:8042/api/power \
  -H 'Content-Type: application/json' \
  -d '{"mode":"sleep"}'
```

Stop only the voice app:

```bash
curl -X POST http://REACHY_HOST:8042/api/app-off \
  -H 'Content-Type: application/json' \
  -d '{"confirm":"off"}'
```

The Pi shutdown endpoint intentionally requires `{"confirm":"shutdown"}`. Do not call it as a routine health test.

## Cooling-maintenance acceptance checklist

After installing or changing a heatsink/fan:

1. Inspect that no cable, camera ribbon, speaker lead, or motor path is pinched.
2. Confirm the heatsink does not contact exposed components or obstruct Reachy's movement.
3. Power on the Pi and verify the fan physically spins under its configured trigger condition.
4. Check current temperature:

   ```bash
   vcgencmd measure_temp
   cat /sys/class/thermal/thermal_zone0/temp
   ```

5. Check Raspberry Pi throttling history:

   ```bash
   vcgencmd get_throttled
   ```

   A clean result is `throttled=0x0`. Nonzero values can include historical undervoltage or thermal events; decode them before concluding that the current state is bad.

6. Start the Reachy daemon and voice app.
7. Confirm daemon restart count, control-loop errors, motor mode, and app status.
8. Leave the robot operating long enough to observe steady-state temperature.
9. Perform the human audio acceptance sequence below.

## Human audio acceptance

1. Leave Reachy in Standby and confirm motors are relaxed.
2. Say **“Hey Hermes”** once at normal speaking volume and distance; repeat the initial-wake check with **“Okay Nabu”** and **“Hey Reachy.”**
3. Ask a simple social question; verify the native Realtime response begins promptly.
4. Interrupt Reachy naturally while it is speaking; verify playback clears and the new turn is heard. In pipeline mode, confirm each configured wake phrase can also interrupt playback.
5. Ask a non-consequential Hermes tool question, such as checking a sensor state.
6. With on-demand camera enabled, ask **“What do you see?”** and verify one camera capture is logged.
7. Start a supervised Kids Mode session, verify the parent lock, one moderated child turn, Flash PCM streaming, parent stop, safe fold, and continued transcript/status redaction.
8. Verify the final answers match the fresh image or Hermes tool result rather than an unverified claim.
9. Exercise Meeting, Standby, and Sleep from the UI.
10. Review logs for tracebacks and record temperature after the test.

## Soak test

Before declaring a deployment stable:

- issue at least 30 app-status requests;
- issue at least 30 daemon-status requests;
- ping Reachy at least 20 times and report packet loss;
- confirm zero new daemon or bridge restarts;
- confirm zero new runtime tracebacks;
- verify the final power state and motor mode.

## Rollback

Keep the previous known-good wheel until acceptance passes.

```bash
curl -X POST http://REACHY_HOST:8000/api/apps/stop-current-app
uv pip install --python /path/to/reachy_mini/.venv/bin/python \
  --reinstall --no-deps /path/to/previous/reachy_mini_hermes.whl
curl -X POST http://REACHY_HOST:8000/api/apps/start-app/reachy_mini_hermes
```

The user configuration is stored separately from the wheel, so rollback normally preserves settings. If a future release changes the configuration schema incompatibly, back up `~/.local/share/reachy_mini_hermes/config.json` before deployment.
