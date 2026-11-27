#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"
start=$(date +%s.%N)
el() { awk -v a="$(date +%s.%N)" -v b="$start" 'BEGIN{printf "%.1f", a-b}'; }
export REDOCLY_SUPPRESS_UPDATE_NOTICE=true

rm -rf results
mkdir -p results

npm ci --no-audit --no-fund > results/npm-ci.log 2>&1 || { tail -30 results/npm-ci.log; exit 1; }
echo "[$(el)s] npm ci done"

npx redocly lint openapi.yaml > results/section2-lint.txt 2>&1
rc=$?
echo "exit code: $rc" >> results/section2-lint.txt
npx redocly lint openapi.yaml --format=json > results/section2-lint.json 2> results/section2-lint-json.stderr.txt
echo "[$(el)s] section 2 lint done (exit code $rc)"

echo "[$(el)s] done" | tee results/total.txt
