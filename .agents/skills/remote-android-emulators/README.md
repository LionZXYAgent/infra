# Remote Android emulators — macOS coding-agent skill

This is an agent skill for a MacBook running Codex or OpenCode, using a remote Simlock gateway in K3s and Android emulator workers on Raspberry Pi 5 and Khadas Edge2.

## Install globally (works with Codex and OpenCode)

```bash
mkdir -p ~/.agents/skills
cp -R remote-android-emulators ~/.agents/skills/
```

Or for a particular repo, copy the folder into `<repo>/.agents/skills/remote-android-emulators/`.

## Prerequisites

- Gateway reachable over HTTPS from the Mac, typically `https://simlock.kulikov.uk` once configured.
- Separate Simlock **agent token** created on the gateway.
- At least one ARM64 worker with an operational Android emulator; Linux ARM64 emulator support is experimental.
- `python3` on the Mac; optional `agent-device >=0.21.28` CLI on Mac and workers, with agent-device enabled in gateway and worker configs. Install the CLI with `npm install -g agent-device@latest` (requires Node.js >=22.12); prefer a version pinned by your project.

```bash
export SIMLOCK_URL="https://simlock.kulikov.uk"
export SIMLOCK_TOKEN="$(security find-generic-password -s simlock-gateway-agent -w)"
# Alternative: export your token manually in a private shell; do not commit it.
python3 ~/.agents/skills/remote-android-emulators/scripts/simlock_api.py identity
```

The `security` example requires you to have already saved the secret in the macOS Keychain with service name `simlock-gateway-agent`; otherwise use your own credential manager.

## Try it

Ask the coding agent:

> Use the remote-android-emulators skill. Run my Android app on a remote emulator, install the local debug APK, take a screenshot, and release the device when finished.

## Compatibility notes

- Simlock **v0.6.0**: HTTP lease/exec, remote agent-device installation and UI control, and reverse/forward port tunnels. **No lease-file upload API** and **no native `simlock tunnel emulator adb`**.
- Later upstream source contains lease-file upload API and native emulator-ADB tunnel. Use only if your *deployed* gateway and workers support them.
- Gnirehtet requires an ADB `localabstract:gnirehtet` reverse mapping. On v0.6.0 the fallback requires explicit SSH access to the actual worker and user VPN approval.

See `SKILL.md` for the end-to-end agent workflow and operational precautions.