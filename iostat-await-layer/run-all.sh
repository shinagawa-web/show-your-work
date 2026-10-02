#!/usr/bin/env bash
# Feasibility check: probe what can be built, then measure.
# Usage: sudo ./run-all.sh   (Linux, root)
set -u
cd "$(dirname "$0")"
export OUT=${OUT:-results}
rm -rf "$OUT"; mkdir -p "$OUT"
start=$(date +%s)
./probe.sh
./measure.sh 2>&1 | tee "$OUT/measure.log"
python3 scripts/summary.py "$OUT" | tee "$OUT/summary.md"
echo "elapsed probe+measure: $(( $(date +%s) - start )) s" | tee -a "$OUT/summary.md"
