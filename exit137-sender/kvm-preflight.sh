#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=${RESULTS:-$here/results}/preflight
mkdir -p "$out"
start=$(date +%s.%N)
{
  "$here/scripts/install-deps.sh" > "$out/install-deps.txt" 2>&1 || { echo "install-deps failed"; exit 1; }
  "$here/scripts/kvm-check.sh" 2>&1 | tee "$out/kvm-check.txt"
  [ "${PIPESTATUS[0]}" -eq 0 ] || { echo "kvm-check failed"; exit 1; }
  trap '"$here/scripts/vm-down.sh"' EXIT
  "$here/scripts/vm-up.sh" 2>&1 | tee "$out/vm-up.txt"
  [ "${PIPESTATUS[0]}" -eq 0 ] || { cp "$here/.vm/serial.log" "$out/serial.log" 2>/dev/null; echo "vm-up failed"; exit 1; }
  echo "\$ (in test VM) uname -a; systemd-detect-virt; nproc; free -m; cloud-init status --wait; systemd-analyze"
  "$here/scripts/vm-ssh.sh" 'uname -a; systemd-detect-virt; nproc; free -m; cloud-init status --wait; systemd-analyze' 2>&1 \
    | tee "$out/in-vm.txt"
  t=$(cat "$here/.vm/t0"); now=$(date +%s.%N)
  awk -v a="$now" -v b="$t" 'BEGIN{printf "cloud_init_done_seconds=%.1f\n", a-b}' | tee -a "$out/vm-up.txt"
  cp "$here/.vm/serial.log" "$out/serial.log"
  end=$(date +%s.%N)
  awk -v a="$end" -v b="$start" 'BEGIN{printf "total_seconds=%.1f\n", a-b}' | tee "$out/total.txt"
} 2>&1 | tee "$out/log.txt"
exit "${PIPESTATUS[0]}"
