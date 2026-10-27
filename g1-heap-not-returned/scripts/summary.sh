#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
results=$1
shift
conds=("$@")
[ ${#conds[@]} -gt 0 ] || conds=($(awk -F'\t' 'NR > 1 { print $1 }' "$here/conditions.tsv"))
python3 "$here/scripts/summarize.py" --md "$results" "${conds[@]}" > "$results/summary.md"
