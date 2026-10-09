#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[ $# -gt 0 ] || set -- baseline shed-150 profile

rm -rf results
mkdir -p results

wait_app() {
  for _ in $(seq 60); do
    curl -sf -o /dev/null http://localhost:3000/light && return 0
    sleep 1
  done
  echo "app did not answer on :3000" >&2
  return 1
}

load() {
  local out=$1 shed=$2
  mkdir -p "$out"
  [ -d node_modules ] || npm ci --no-audit --no-fund
  SHED_THRESHOLD_MS=$shed docker compose --profile base up -d --build
  trap 'docker compose --profile base down' EXIT
  wait_app
  CSV_OUT="$out/results.csv" node loadgen/ramp.js exp01
  docker compose --profile base down
  trap - EXIT
}

profile() {
  local out=results/profile sync_ms=200
  mkdir -p "$out"
  rm -f app/isolate-*.log
  SYNC_MS=$sync_ms scripts/profile.sh | tee "$out/profile.txt"
  mv app/isolate-*.log "$out/"
  {
    echo "## V8 CPU Profile — blockEventLoop (sync_ms=$sync_ms)"
    echo '```'
    awk '/\[Bottom up/ {f=1} f {print; if (++n == 60) exit}' "$out/profile.txt"
    echo '```'
  } > "$out/summary.md"
}

for c in "$@"; do
  case "$c" in
    baseline)
      load results/baseline ""
      node scripts/plot.js results/baseline/results.csv > results/baseline/summary.md
      ;;
    shed-*)
      ms=${c#shed-}
      out=results/shed-${ms}ms
      load "$out" "$ms"
      { echo "## Shedding threshold: ${ms}ms"; node scripts/plot.js "$out/results.csv"; } > "$out/summary.md"
      ;;
    profile)
      profile
      ;;
    *)
      echo "unknown condition: $c (baseline, shed-<ms>, profile)" >&2
      exit 2
      ;;
  esac
done
