#!/usr/bin/env bash
# Destructive setup belongs only on an explicitly disposable, fresh Linux VM.
set -euo pipefail
set +x
umask 077
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ ${1:-} == --disposable && $# == 2 ]] || fail 'Use --disposable REPORT_DIRECTORY on a fresh VM.'
[[ $(uname -s) == Linux ]] || fail 'A disposable Ubuntu/Debian VM is required.'
if command -v k3s >/dev/null 2>&1; then fail 'Existing K3s installation; refusing deployment.'; fi
[[ ! -e /etc/rancher/k3s ]] || fail 'Existing K3s configuration; refusing deployment.'
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
source "$ROOT/bootstrap/versions.env"
export PATH="$HOME/.local/bin:$PATH"
report=$2
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
node_ip=$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')
[[ -n $node_ip ]] || fail 'No assigned IPv4 address found.'
bash "$ROOT/scripts/install-tools.sh" flux sops kubectl
bash "$ROOT/scripts/provision-server.sh" "$node_ip" ci-infra "$K3S_VERSION" "$work/kubeconfig"
python3 "$ROOT/tests/ci/deployment.py" --source "$ROOT" --kubeconfig "$work/kubeconfig" \
  --node-ip "$node_ip" --report-dir "$report"
