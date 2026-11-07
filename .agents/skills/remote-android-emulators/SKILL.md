---
name: remote-android-emulators
description: Use remote Android emulators from a macOS coding agent through the Simlock gateway, backed by Raspberry Pi 5 and Khadas Edge2 workers. Lease and release devices, install local APKs, inspect and control Android UI, run ADB commands, collect screenshots and logs, and optionally route the emulator's IPv4 traffic through the Mac with Gnirehtet. Use for Android builds, UI tests, Appium or Maestro setup, and emulator troubleshooting.
compatibility: macOS; Codex and OpenCode; Simlock gateway/worker architecture (baseline v0.6.0)
---

# Remote Android emulators from macOS

Use **the existing Simlock gateway** for device lifecycle and per-lease access. The gateway runs in the user's K3s/Flux infrastructure (`LionZXYOrg/infra`); its remote Linux ARM64 workers are **Raspberry Pi 5** and **Khadas Edge2**. The Mac is a CLIENT: do **not** start an Android Emulator, a local Simlock worker, or a second gateway on the Mac.

## Environment and non-negotiable rules

- Gateway: `https://simlock.kulikov.uk` **only if DNS and TLS are configured**. Never assume the address is live; verify `/v1/status` or `/v1/identity` before using it.
- Read `SIMLOCK_URL` and `SIMLOCK_TOKEN` from the current environment. `SIMLOCK_TOKEN` must be a **gateway-issued agent token**, not a join token, admin token, worker token, or token from an unrelated worker. If missing, ask the user to set it through a secure local mechanism; do not guess or print it.
- Keep one distinct gateway agent token and one `AGENT_DEVICE_SESSION` per independent agent. By default each token can hold only one lease. Never share or overwrite another agent's session.
- Treat any leased device as ephemeral: emulator and lease data are removed upon release. Do not persist user credentials, private APKs, or PII in logs/screenshots.
- Always release a device after the task, including after failure. Renew long-lived HTTP leases before expiration. **Do not release a lease controlled by another agent**.
- Do not use unleased devices; never run a blanket `adb devices`, `adb -e`, `adb -d`, `adb kill-server`, or `simlock nuke` as a test cleanup action. Avoid direct unmanaged worker ADB ports.
- **Do not expose** ADB (5037/5038), emulator ports (5554/5555), SSH, or the Gnirehtet relay (31416) to the internet.
- SDK compatibility is not guaranteed on ARM64 Linux: if no worker advertises a bootable Android image, report this explicitly. Don't fake success or install Android SDK images from the Mac hoping to fix workers.

## Decide which interface to use

1. **Preferred for coding agents and APK + UI tests: `agent-device` proxy**. It uploads the Mac's APK automatically, offers snapshots/screenshots/UI commands, and owns its lease. Simlock **v0.6.0** already supports this, provided `agent-device >=0.21.28` is installed and enabled on the gateway and workers.
2. **Use Simlock's HTTP API** for catalog, queue, explicit leases, ADB commands and diagnostic logs. `exec` runs ADB on the correct ARM64 worker, not on the Mac; a path supplied to ADB refers to a file on that worker.
3. **Use `simlock tunnel emulator adb` only after verifying the deployed Simlock version supports it.** This tunnel appears in newer upstream docs but **not v0.6.0**. With it, existing macOS ADB tools, Maestro, or Appium can connect to the remote emulator. Do not assume this command exists on pinned v0.6.0.
4. **Use Gnirehtet only on explicit request** to send the Android emulator's IPv4 traffic through the Mac; it is not needed for normal ADB or APK install. See below.

## Preparation on the Mac (once)

```bash
# On first setup, with Node.js >= 22.12 available:
# npm install -g agent-device@latest
# Use an installed/pinned version >= 0.21.28 (agent should not upgrade without approval).
command -v agent-device || echo 'Install agent-device CLI for UI control' 
export SIMLOCK_URL="${SIMLOCK_URL:-https://simlock.kulikov.uk}"
: "${SIMLOCK_TOKEN:?Set a gateway-issued agent token before continuing}"
python3 scripts/simlock_api.py identity
python3 scripts/simlock_api.py catalog
```

The script path above is relative to **this skill directory**. From other working directories, invoke `<skill-directory>/scripts/simlock_api.py` (or use its full path). The helper uses Python 3 stdlib and does not install dependencies.

If the gateway's `identity` or `catalog` call fails: stop and report the HTTP status / TLS / permissions / missing capacity, not a made-up emulator.

## Workflow A: agent-device UI test (recommended)

Prerequisite on **both workers**: installed `agent-device >=0.21.28`; worker setting `capabilities.agent-device.enabled=true` (joined workers default off). On the **gateway**: `capabilities.agent-device.enabled=true`. Operators must restart affected daemon services and check `simlock --gateway worker list` reports `agent-device available` for each worker. Do not change production configuration unless asked.

On **Mac**, run:

```bash
export AGENT_DEVICE_DAEMON_AUTH_TOKEN="$SIMLOCK_TOKEN"
export AGENT_DEVICE_SESSION="android-${USER}-$(date +%s)"   # distinct per task/agent
agent-device connect proxy \
  --daemon-base-url "${SIMLOCK_URL%/}/v1/agent-device"
agent-device open --platform android
agent-device devices
# app identifier below is the real Android package name, NOT a display name
agent-device install com.example.app ./app/build/outputs/apk/debug/app-debug.apk
agent-device open com.example.app
agent-device snapshot
agent-device screenshot /tmp/android-ui.png
# Inspect screenshot and UI snapshot; perform only needed interactions.
agent-device close
```

- APK **path** is local to macOS. `agent-device install` uploads it automatically to the leased worker and installs it.
- `agent-device open --platform android` allocates an emulator; `agent-device open com.example.app` launches an installed Android package. They are different operations.
- Use `--os 35` on `agent-device open` only if an Android API 35 image is reported as installed. Prefer `google_apis` rather than `google_apis_playstore` if planning to attach local ADB through a future native tunnel.
- `agent-device` owns this lease. Don't also obtain a second raw HTTP lease or attempt raw `release` unless intentionally recovering an abandoned session.
- Keep the task session alive through operations; `agent-device close` is required in `finally`/cleanup logic. It uses the session named by `AGENT_DEVICE_SESSION`; do not close a different session.
- If `agent-device` is unavailable on the worker, switch to Workflow B for ADB-only operations; do **not** assume its upload path is supported.

## Workflow B: explicit HTTP lease + remote ADB (v0.6.0 compatible)

The bundled stdlib helper handles request polling, lease ID routing and parsing streaming `exec` events. Invoke from its directory or with its absolute path.

```bash
python3 scripts/simlock_api.py status
python3 scripts/simlock_api.py catalog
LEASE=$(python3 scripts/simlock_api.py lease --os 35 --id-only)
# If API 35 is not available, omit --os and use a catalog-advertised image.
python3 scripts/simlock_api.py adb "$LEASE" shell getprop ro.build.version.release
python3 scripts/simlock_api.py adb "$LEASE" shell wm size
python3 scripts/simlock_api.py adb "$LEASE" shell input tap 400 600
python3 scripts/simlock_api.py adb "$LEASE" logcat -d -t 150
python3 scripts/simlock_api.py renew "$LEASE"
python3 scripts/simlock_api.py release "$LEASE"
```

In actual tasks ensure release in a `trap` / `finally` and renew before TTL. Never assume that the manually leased emulator is on a particular worker: the gateway chooses Pi 5 or Edge2 and the lease ID routes requests. Preserve the **complete** lease ID as given by the gateway.

For raw `exec`, arguments are passed as a JSON `args` list; do not include `-s`, another device ID, or interactive `adb shell` without a command. Binary stdout is not usable for image capture on v0.6.0: use agent-device screenshot when supported. The HTTP API on v0.6.0 **cannot upload an APK**. Do not call the lease-file API unless a newer deployed version actually supports it.

## App installation options

- **Preferred on v0.6.0:** `agent-device install <package-id> <mac-local-apk-path>` as above.
- **Manual v0.6.0 fallback:** arrange a deliberate transfer to the correct worker (e.g. securely `scp` a local APK over SSH if SSH access is authorized), then `python3 scripts/simlock_api.py adb "$LEASE" install -r /absolute/path/on/worker/app.apk`. Remove the staged file afterwards. Verify the worker identity from the actual lease metadata; never assume Pi vs Edge2 based on availability or a hard-coded rotation.
- **Newer Simlock only:** upload raw bytes to `PUT /v1/leases/$LEASE/files/app.apk` with `curl --data-binary @local.apk`, then remote-exec `adb install -r app.apk`. Lease file APIs were added AFTER the published v0.6.0 release. Verify the deployed version, and set proxy upload-body limits large enough before relying on this (Traefik/middlewares/CDN may impose limits).
- **Newer native ADB tunnel only:** run `simlock tunnel emulator adb --lease "$LEASE"` (long-running) and use the reported `127.0.0.1:<port>` serial with `adb -s ... install -r ./local.apk`. The tunnel must be enabled on gateway and worker. Never assume an example port is the actual port.

## Optional: route Android internet via macOS with Gnirehtet

Use only if explicitly asked. Gnirehtet carries Android **IPv4 TCP/UDP** (not IPv6) through the Mac's socket/network/VPN routing, using an Android `VpnService` and `adb reverse localabstract:gnirehtet tcp:31416`. User approval of Android's VPN permission may be required. Gnirehtet is lightly maintained; do not assume it works with every Android image or VPN policy.

### Newer Simlock with native ADB tunnel

```bash
brew install gnirehtet android-platform-tools
export SIMLOCK_URL SIMLOCK_TOKEN LEASE
simlock tunnel emulator adb --lease "$LEASE"     # keep running; copy actual localhost:<port>
# In another terminal:
gnirehtet relay                                     # keep running
SERIAL='127.0.0.1:ACTUAL_PORT'
gnirehtet install "$SERIAL"
gnirehtet start "$SERIAL"
# Approve the Android VPN dialog; verify with a request from INSIDE Android.
# Cleanup: gnirehtet stop "$SERIAL"; stop relay and tunnel; release lease.
```

This native-ADB tunnel command is **NOT in v0.6.0**. If it fails with unknown tunnel kind, stop and use the separate SSH fallback below if specifically authorized.

### v0.6.0 Gnirehtet fallback (only with authorized worker SSH)

Because the v0.6.0 Simlock reverse tunnel is **not** an arbitrary ADB `localabstract` tunnel, don't mistake `simlock tunnel reverse 31416` for Gnirehtet's required `adb reverse localabstract:gnirehtet tcp:31416`.

1. Start `gnirehtet relay` **on the Mac** (listens on Mac localhost:31416).
2. From the Mac, establish SSH **reverse** forwarding to the actual worker holding the lease, in a separate long-running session:
   `ssh -N -o ExitOnForwardFailure=yes -R 127.0.0.1:31416:127.0.0.1:31416 <authorized-user>@<actual-worker-lan-host>`.
3. Find `gnirehtet.apk` locally using `brew list gnirehtet | grep '/gnirehtet.apk$'` or from the official archive. Copy it securely to a temporary directory on **that same** worker (e.g. `scp ... <worker>:/tmp/gnirehtet-<session>.apk`).
4. Through `simlock_api.py adb "$LEASE" ...`, execute `install -r /tmp/gnirehtet-<session>.apk`, then `reverse localabstract:gnirehtet tcp:31416`, then `shell am start -a com.genymobile.gnirehtet.START -n com.genymobile.gnirehtet/.GnirehtetActivity`.
5. Approve the Android VPN dialog. Verify Android's egress, not merely the worker's or Mac's IP. On cleanup, send Gnirehtet STOP, remove its `adb reverse` mapping, close SSH, stop relay, delete worker APK, and release the lease.

**Caveats:** one fixed `31416` listener per worker may conflict with another concurrent Gnirehtet user; do not hijack another agent's port or relay. A sleeping Mac or disconnected tunnel drops traffic; Android VPN might block connectivity until restarted. No local system routing change is promised for IPv6.

## Test completion checklist

1. Report the gateway endpoint (not token), lease ID and platform/Android API level; report the worker label if actually returned.
2. Confirm APK installation or clearly identify the failure; gather `agent-device snapshot`/screenshot or remote ADB output as evidence.
3. If networking was requested, test connectivity **from inside Android** and note IPv4-only Gnirehtet behavior.
4. Release the lease/session, stop local tunnels/relays, and leave unrelated worker devices untouched.
5. Report blockers honestly (gateway down, no images, unsupported ARM64 emulator, denied permissions, no agent-device, old Simlock).

## Sources / version references

- Simlock v0.6.0 remote agents: https://github.com/callstackincubator/simlock/blob/v0.6.0/website/src/content/docs/docs/using-devices/remote-agents.mdx
- Simlock v0.6.0 agent-device: https://github.com/callstackincubator/simlock/blob/v0.6.0/website/src/content/docs/docs/using-devices/agent-device.mdx
- Newer Simlock main docs (verify features against installed version): https://github.com/callstackincubator/simlock/blob/main/website/src/content/docs/docs/using-devices/remote-agents.mdx
- Gnirehtet: https://github.com/Genymobile/gnirehtet