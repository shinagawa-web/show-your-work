#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
claim "issue #364 section 10 (limits of applicability): in Docker, a systemd service and Kubernetes, the CPU limit appears as cpu.max quota and cpu.stat can be read on the host and inside the container (no load)"
exec_=${EXEC-limactl shell cpu-quota-throttling-p99 --}
probe() { $exec_ sudo bash -c "$(cat "$root/scripts/env_probe.sh")" _ "$@"; }
out=$results/s10
mkdir -p "$out"
: > "$out/env.jsonl"
probe state before | tee -a "$out/env.jsonl"
probe docker | tee -a "$out/env.jsonl"
probe systemd | tee -a "$out/env.jsonl"
if [ "${SKIP_K3S:-0}" != 1 ]; then
  trap 'probe k3s-uninstall || true' EXIT
  probe k3s | tee -a "$out/env.jsonl"
  probe k3s-uninstall
  trap - EXIT
  probe state after_k3s_uninstall | tee -a "$out/env.jsonl"
fi
report md s10 "$out/env.jsonl"
