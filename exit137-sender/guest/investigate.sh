#!/usr/bin/env bash
# Runs as root inside the test VM after setup.sh. Runs the given steps in
# order, once each. Steps: any condition (C1 ... C11), or
#   G0       host-wide OOM caused by a process outside Docker (no container)
#   RESTART  restart containerd and dockerd
# Run directories are numbered per condition in the order they ran.
set -uo pipefail
. "$(dirname "$0")/conditions.sh"

G0() {
  sync; echo 3 > /proc/sys/vm/drop_caches
  echo "MemAvailable=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)MiB" > "$dir/sizing.txt"
  "$(dirname "$0")/subject" alloc -1 4 5 0 > "$dir/process.log" 2>&1
  echo "exit status $?" >> "$dir/process.log"
  sleep 0.5
  journalctl -k --after-cursor="$kc" -o short-iso-precise -q > "$dir/kernel.txt"
  grep -E 'invoked oom-killer|oom-kill:|Killed process' "$dir/kernel.txt" | tee "$dir/oom.txt"
}
RESTART() {
  systemctl restart containerd docker
  systemctl is-active containerd docker | tee "$dir/status.txt"
}

if [ "${TRACE:-0}" = 1 ]; then
  ( echo -1000 > /proc/self/oom_score_adj; BPFTRACE_MAX_STRLEN=128 exec bpftrace "$(dirname "$0")/trace.bt" ) > "$results/trace.txt" 2>&1 &
  trace_pid=$!
  for _ in $(seq 100); do grep -q Attaching "$results/trace.txt" && break; sleep 0.2; done
  sleep 1
  head -3 "$results/trace.txt"
  kill -0 "$trace_pid" || { echo "bpftrace failed"; exit 1; }
fi
if [ "${LOCKSHIM:-0}" = 1 ]; then
  # Keep the shim binary and its libraries in memory (mlock), so the shim
  # does not fault its code back in from disk under memory pressure.
  shim_files=$(ldd /usr/bin/containerd-shim-runc-v2 | awk '/=>/ {print $3} /^\t\// {print $1}')
  echo -1000 > /proc/self/oom_score_adj
  vmtouch -l -d -P "$results/vmtouch.pid" /usr/bin/containerd-shim-runc-v2 $shim_files </dev/null >/dev/null 2>&1
  echo 0 > /proc/self/oom_score_adj
  sleep 1
  { ldd /usr/bin/containerd-shim-runc-v2; vmtouch /usr/bin/containerd-shim-runc-v2 $shim_files; } | tee "$results/vmtouch.txt"
fi
declare -A seen
for step in "$@"; do
  seen[$step]=$(( ${seen[$step]:-0} + 1 ))
  begin "$step" "${seen[$step]}"
  "$step"
  echo "$step run ${seen[$step]} seconds=$(el "$t_start")" | tee -a "$results/timing.txt"
done
[ -n "${trace_pid:-}" ] && { sleep 1; kill -INT "$trace_pid"; wait "$trace_pid"; }
[ -f "$results/vmtouch.pid" ] && kill "$(cat "$results/vmtouch.pid")"
python3 "$(dirname "$0")/summarize.py" "$results" >/dev/null 2>&1
grep -E '^\| C' "$results/summary.md" | cut -d'|' -f2-9
