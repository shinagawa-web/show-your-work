#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
s() { timeout 10 "$here/scripts/vm-ssh.sh" "$@"; }
old=$(s cat /proc/sys/kernel/random/boot_id) || { echo "cannot read boot_id before reboot"; exit 1; }
t0=$(date +%s.%N)
s 'sudo systemctl reboot' >/dev/null 2>&1
deadline=$(( $(date +%s) + ${VM_BOOT_TIMEOUT:-240} ))
until new=$(s cat /proc/sys/kernel/random/boot_id 2>/dev/null) && [ -n "$new" ] && [ "$new" != "$old" ]; do
  [ "$(date +%s)" -lt "$deadline" ] || { echo "test VM did not come back within ${VM_BOOT_TIMEOUT:-240}s"; exit 1; }
  sleep 1
done
state=$(timeout 120 "$here/scripts/vm-ssh.sh" 'systemctl is-system-running --wait')
active=$(s 'systemctl is-active docker')
awk -v a="$(date +%s.%N)" -v b="$t0" -v o="$old" -v n="$new" -v st="$state" -v ac="$active" \
  'BEGIN{printf "boot_id %s -> %s system=%s docker=%s reboot_seconds=%.1f\n", o, n, st, ac, a-b}'
[ "$active" = active ] || { echo "docker is not active after reboot"; exit 1; }
