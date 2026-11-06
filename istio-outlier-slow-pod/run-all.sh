#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
conds=(5xx-default 5xx-34 slow-34 slow-client-200ms route-20ms route-200ms route-1000ms)
[ "${1:-}" = --list ] && { printf '%s\n' "${conds[@]}"; exit 0; }
[ $# -gt 0 ] && conds=("$@")
rm -rf "$results"
mkdir -p "$results"
. "$here/scripts/versions.env"
start=$(date +%s)
"$here/scripts/setup.sh" > "$results/setup.log" 2>&1 || { tail -30 "$results/setup.log"; echo "RUN INVALID: setup.sh failed" | tee "$results/summary.md"; exit 1; }
echo "setup $(( $(date +%s) - start ))s" >> "$results/elapsed.txt"
for c in "${conds[@]}"; do
  s=$(date +%s)
  "$here/scripts/cond.sh" "$c" "$results/$c"
  echo "$c $(( $(date +%s) - s ))s" >> "$results/elapsed.txt"
done
"$here/.bin/kind" delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
python3 -B "$here/scripts/report.py" "$results" "${conds[@]}" > "$results/summary.md"
cat "$results/summary.md"
! grep -q '^RUN INVALID' "$results/summary.md"
