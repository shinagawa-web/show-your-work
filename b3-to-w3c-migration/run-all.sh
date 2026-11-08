#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"
here=$PWD
start=$(date +%s.%N)
el() { awk -v a="$(date +%s.%N)" -v b="$start" 'BEGIN{printf "%.1f", a-b}'; }

[ $# -gt 0 ] || set -- 3 5
conds=()
for t in "$@"; do
  found=$(awk -F'\t' -v t="$t" 'NR > 1 && ($1 == t || $2 == t) { print $1 }' conditions.tsv)
  [ -n "$found" ] || { echo "unknown section or condition: $t" >&2; exit 2; }
  conds+=($found)
done

rm -rf results
mkdir -p results
set -a
. scripts/versions.env
set +a

for img in "$ENVOY_IMAGE" "$JAEGER_IMAGE" "$POSTGRES_IMAGE"; do
  docker pull -q "$img" > /dev/null || { echo "RUN INVALID: pull $img failed" | tee results/summary.md; exit 1; }
done
docker build -q -t b3-to-w3c-migration-app:local app > /dev/null || { echo "RUN INVALID: app build failed" | tee results/summary.md; exit 1; }
echo "[$(el)s] images ready"

status=0
for c in "${conds[@]}"; do
  scripts/run-condition.sh "$c"
  [ -f "results/$c/analysis.json" ] || { echo "no analysis for $c" >&2; status=1; }
  echo "[$(el)s] $c"
done

python3 -B scripts/summary.py "$here/results" > results/summary.md || status=1
cat results/summary.md
echo "[$(el)s] done" | tee results/total.txt
exit "$status"
