#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"
here=$(pwd)
start=$(date +%s.%N)
el() { awk -v a="$(date +%s.%N)" -v b="$start" 'BEGIN{printf "%.1f", a-b}'; }
export REDOCLY_SUPPRESS_UPDATE_NOTICE=true

rm -rf results
mkdir -p results/work

npm ci --no-audit --no-fund > results/npm-ci.log 2>&1 || { tail -30 results/npm-ci.log; exit 1; }
echo "[$(el)s] npm ci done"

workdir() {
  local d=$here/results/work/$1
  mkdir -p "$d"
  cp openapi.yaml "$d/"
  echo "$d"
}

lint() {
  local dir=$1 out=$2 rc
  (cd "$dir" && npx redocly lint openapi.yaml) > "results/$out.txt" 2>&1
  rc=$?
  echo "exit code: $rc" >> "results/$out.txt"
  echo "[$(el)s] $out done (exit code $rc)"
}

lint_json() {
  local dir=$1 out=$2
  (cd "$dir" && npx redocly lint openapi.yaml --format=json) > "results/$out.json" 2> "results/$out-json.stderr.txt"
}

s2=$(workdir section2)
lint "$s2" section2-lint
lint_json "$s2" section2-lint

s3=$(workdir section3)
cp redocly.yaml "$s3/"
lint "$s3" section3-lint
lint_json "$s3" section3-lint

s3n=$(workdir section3-without-extends)
awk '/^extends:/ { skip = 1; next } skip && /^  - / { next } { skip = 0; print }' redocly.yaml > "$s3n/redocly.yaml"
lint "$s3n" section3-lint-without-extends

s4=$(workdir section4)
cp redocly.yaml "$s4/"
(cd "$s4" && npx redocly lint --generate-ignore-file openapi.yaml) > results/section4-generate.txt 2>&1
echo "exit code: $?" >> results/section4-generate.txt
cp "$s4/.redocly.lint-ignore.yaml" results/section4-lint-ignore.yaml || echo "no .redocly.lint-ignore.yaml was generated" >&2
lint "$s4" section4-lint
lint_json "$s4" section4-lint

echo "[$(el)s] done" | tee results/total.txt
