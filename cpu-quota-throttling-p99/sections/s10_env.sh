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
python3 - "$out/env.jsonl" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
cols = ["env", "limit_setting", "host_cgroup", "host_cpu.max", "host_cpu.stat", "host_cpu.stat_after_exec", "pod_cgroup", "pod_cpu.max", "inside_cpu.max", "inside_cpu.stat", "cpu.max_same", "cpu.stat_same"]
print("| " + " | ".join(cols) + " |")
print("|" + "---|" * len(cols))
for r in rows:
    if "env" in r:
        print("| " + " | ".join(str(r.get(c, "-")).strip() for c in cols) + " |")
PY
