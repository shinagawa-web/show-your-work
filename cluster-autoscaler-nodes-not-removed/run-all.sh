#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
conds=(base p1 p2 p3 p4 p5 p6 p7 p8 p9 p10 p11 p12 p13 p14 p15 p16 p17 p19)
[ "${1:-}" = --list ] && { printf '%s\n' "${conds[@]}"; exit 0; }
[ $# -gt 0 ] && conds=("$@")
rm -rf "$results"
mkdir -p "$results"
for c in "${conds[@]}"; do
  start=$(date +%s)
  "$here/scripts/cond.sh" "$c" "$results/$c"
  echo "$c $(( $(date +%s) - start ))s" >> "$results/elapsed.txt"
done
"$here/.bin/kind" delete cluster --name ca >/dev/null 2>&1 || true
python3 -B "$here/scripts/checks.py" "$results" "${conds[@]}" > "$results/summary.md"
cat "$results/summary.md"
! grep -q '^RUN INVALID' "$results/summary.md"
