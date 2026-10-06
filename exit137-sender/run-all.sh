#!/usr/bin/env bash
# Linux layer: start the test VM with QEMU+KVM, run every condition inside it
# and copy the raw output back to results/.
#   run-all.sh [COND...]
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
start=$(date +%s.%N)
rm -rf "$results"; mkdir -p "$results"
el() { awk -v a="$(date +%s.%N)" -v b="$start" 'BEGIN{printf "%.1f", a-b}'; }

"$here/scripts/install-deps.sh" > "$results/install-deps.txt" 2>&1 || { echo "install-deps failed"; exit 1; }
echo "[$(el)s] deps installed"
gcc -O2 -static -Wall -o "$here/guest/subject" "$here/subject/subject.c" || exit 1
gcc -O2 -static -Wall -o "$here/guest/cgprobe" "$here/subject/cgprobe.c" || exit 1
install -m 0755 /bin/busybox "$here/guest/busybox"
echo "[$(el)s] subject built"
VM_MEM=${VM_MEM:-512} "$here/scripts/vm-up.sh" | tee "$results/vm-up.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || { cp "$here/.vm/serial.log" "$results/" 2>/dev/null; exit 1; }
trap '"$here/scripts/vm-down.sh"' EXIT
echo "[$(el)s] test VM up"
cp "$here/.vm/qemu.txt" "$results/qemu.txt"
tar -C "$here" -c guest | "$here/scripts/vm-ssh.sh" 'rm -rf ~/w && mkdir -p ~/w && tar -C ~/w -x'
"$here/scripts/vm-ssh.sh" "sudo DEBUG=${DEBUG:-0} TRACE=${TRACE:-0} LOCKSHIM=${LOCKSHIM:-0} ~/w/guest/setup.sh" > "$results/setup.txt" 2>&1 || { tail -30 "$results/setup.txt"; exit 1; }
grep seconds "$results/setup.txt"
echo "[$(el)s] test VM set up"
"$here/scripts/vm-ssh.sh" "sudo PROBE=${PROBE:-0} TRACE=${TRACE:-0} TRACE_SHIM=${TRACE_SHIM:-0} LOCKSHIM=${LOCKSHIM:-0} ~/w/guest/${GUEST_SCRIPT:-conditions.sh} $*" > "$results/conditions.txt" 2>&1
grep -E "seconds|^==" "$results/conditions.txt"
"$here/scripts/vm-ssh.sh" 'sudo python3 ~/w/guest/summarize.py /root/results' > /dev/null
"$here/scripts/vm-ssh.sh" 'sudo tar -C /root/results -c .' | tar -C "$results" -x
echo "[$(el)s] done" | tee "$results/total.txt"
