#!/bin/sh
set -e

WDIR="$(cd "$(dirname "$0")" && pwd)"
RATE=${RATE:-200}
SYNC_MS=${SYNC_MS:-30000}
DURATION=${DURATION:-60}
RESULTS="$WDIR/results/oom-timeseries.tsv"

mkdir -p "$(dirname "$RESULTS")"

cd "$WDIR"

docker compose --profile go-queue down --remove-orphans 2>/dev/null || true

echo "--- starting go-app in Docker (mem_limit=128m) ---"
docker compose build --no-cache go-app
docker compose --profile go-queue up -d go-app

echo "--- waiting for ready ---"
for i in $(seq 1 30); do
  if curl -sf http://localhost:3000/light > /dev/null 2>&1; then
    break
  fi
  sleep 1
done

CONTAINER=$(docker compose --profile go-queue ps -q go-app)
echo "container=$CONTAINER"

echo "--- collecting time series until OOM (RATE=$RATE req/s, SYNC_MS=${SYNC_MS}ms) ---"
docker logs -f "$CONTAINER" 2>&1 | grep --line-buffered '^ts=' > "$RESULTS" &
LOGS_PID=$!

RATE=$RATE SYNC_MS=$SYNC_MS DURATION=$DURATION node "$WDIR/loadgen/open-loop.js" || true

sleep 3
kill $LOGS_PID 2>/dev/null || true

EXIT_CODE=$(docker inspect --format='{{.State.ExitCode}}' "$CONTAINER" 2>/dev/null || echo "unknown")
OOM=$(docker inspect --format='{{.State.OOMKilled}}' "$CONTAINER" 2>/dev/null || echo "unknown")
echo "exit_code=$EXIT_CODE oom_killed=$OOM"

docker compose --profile go-queue down 2>/dev/null || true

echo "--- results ---"
cat "$RESULTS"
