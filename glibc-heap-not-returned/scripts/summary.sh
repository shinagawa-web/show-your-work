#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
results=$1
python3 "$here/scripts/summarize.py" "$results" > "$results/summary.md"
