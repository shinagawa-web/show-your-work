#!/usr/bin/env bash
# Stop the test VM.
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
vmdir=${VMDIR:-$here/.vm}
[ -f "$vmdir/qemu.pid" ] || exit 0
pid=$(cat "$vmdir/qemu.pid")
kill "$pid" 2>/dev/null
for _ in $(seq 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
kill -9 "$pid" 2>/dev/null
rm -f "$vmdir/qemu.pid"
