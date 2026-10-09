#!/usr/bin/env bash
set -euo pipefail

LAB_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SYNC_MS="${SYNC_MS:-200}"
LOAD_DURATION="${LOAD_DURATION:-20}"
CONCURRENCY="${CONCURRENCY:-10}"
PORT="${PORT:-3000}"

cd "$LAB_ROOT/app"
npm ci --silent --no-audit --no-fund

node --prof index.js &
APP_PID=$!
trap "kill $APP_PID 2>/dev/null; wait $APP_PID 2>/dev/null || true" EXIT

for _ in $(seq 60); do
  curl -sf -o /dev/null "http://localhost:${PORT}/light" && break
  kill -0 "$APP_PID" 2>/dev/null || { echo "ERROR: app exited" >&2; exit 1; }
  sleep 1
done
curl -sf -o /dev/null "http://localhost:${PORT}/light" || { echo "ERROR: app did not answer on :${PORT}" >&2; exit 1; }

node -e "
const http = require('http')
const end = Date.now() + 5000
function send() {
  if (Date.now() >= end) return
  http.get('http://localhost:${PORT}/sync-cpu?ms=${SYNC_MS}', r => { r.resume(); send() }).on('error', send)
}
for (let i = 0; i < ${CONCURRENCY}; i++) send()
setTimeout(() => {}, 5000)
" 2>/dev/null || true

node -e "
const http = require('http')
const end = Date.now() + ${LOAD_DURATION}000
let active = 0
function send() {
  if (Date.now() >= end) { if (--active === 0) process.exit(0); return }
  active++
  http.get('http://localhost:${PORT}/sync-cpu?ms=${SYNC_MS}', r => { r.resume(); send() }).on('error', send)
}
for (let i = 0; i < ${CONCURRENCY}; i++) { active++; send() }
setTimeout(() => process.exit(0), ${LOAD_DURATION}000 + 5000)
" 2>/dev/null || true

kill "$APP_PID" 2>/dev/null || true
wait "$APP_PID" 2>/dev/null || true
trap - EXIT

ISOLATE=$(ls "$LAB_ROOT/app"/isolate-*.log 2>/dev/null | head -1)
if [ -z "$ISOLATE" ]; then
  echo "ERROR: no isolate log found" >&2
  exit 1
fi

echo "=== V8 CPU profile: /sync-cpu?ms=${SYNC_MS}, concurrency=${CONCURRENCY}, duration=${LOAD_DURATION}s ==="
echo ""
node --prof-process "$ISOLATE" 2>/dev/null
