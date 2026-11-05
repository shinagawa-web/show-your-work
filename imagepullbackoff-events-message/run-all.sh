#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
parts=(causes)
[ "${1:-}" = --list ] && { printf '%s\n' "${parts[@]}"; exit 0; }
[ $# -gt 0 ] && parts=("$@")
export PATH=$here/.bin:$PATH
rm -rf "$results"
mkdir -p "$results"
abort() { echo "RUN INVALID: $1" | tee "$results/summary.md"; exit 1; }
s0=$(date +%s)
"$here/scripts/setup.sh" > "$results/setup.txt" 2>&1 || abort "setup.sh failed (setup.txt)"
echo "setup.sh $(( $(date +%s) - s0 ))s" >> "$results/elapsed.txt"
for p in "${parts[@]}"; do
  start=$(date +%s)
  case $p in
    causes) "$here/scripts/causes.sh" "$results/$p" ;;
    *) abort "unknown part $p" ;;
  esac
  echo "$p $(( $(date +%s) - start ))s" >> "$results/elapsed.txt"
done
python3 -B "$here/scripts/summary.py" "$results" "${parts[@]}" > "$results/summary.md" || abort "summary.py failed"
cat "$results/summary.md"
! grep -q '^RUN INVALID' "$results/summary.md"
