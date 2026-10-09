#!/bin/sh
set -e

WDIR="$(cd "$(dirname "$0")" && pwd)"
RATE=${RATE:-50}
SYNC_MS=${SYNC_MS:-10000}
DURATION=${DURATION:-30}
RESULTS="$WDIR/results/rss-timeseries.tsv"
BIN="/tmp/go-queue-server-$$"

mkdir -p "$(dirname "$RESULTS")"

echo "--- building ---"
cd "$WDIR"
go build -o "$BIN" .

echo "--- starting server (SYNC_MS=$SYNC_MS) ---"
SYNC_MS=$SYNC_MS PORT=3000 "$BIN" 2> "$RESULTS" &
SERVER_PID=$!

echo "--- waiting for ready ---"
for i in $(seq 1 30); do
  if curl -sf http://localhost:3000/light > /dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "--- collecting time series (RATE=$RATE req/s, SYNC_MS=${SYNC_MS}ms, DURATION=${DURATION}s) ---"
cd "$WDIR"
RATE=$RATE SYNC_MS=$SYNC_MS DURATION=$DURATION node "$WDIR/loadgen/open-loop.js"

kill $SERVER_PID 2>/dev/null || true
rm -f "$BIN"

echo "--- results saved to $RESULTS ---"
grep '^ts=' "$RESULTS" || echo "(no ts= lines captured)"
