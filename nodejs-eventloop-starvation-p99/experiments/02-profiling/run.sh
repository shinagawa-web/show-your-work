#!/bin/bash
set -euo pipefail

LAB_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SYNC_MS="${SYNC_MS:-200}"
LOAD_DURATION="${LOAD_DURATION:-20}"
CONCURRENCY="${CONCURRENCY:-10}"
PORT="${PORT:-3000}"

cd "$LAB_ROOT/app"
npm install --silent

# Start app with V8 CPU profiler
node --prof index.js &
APP_PID=$!
trap "kill $APP_PID 2>/dev/null; wait $APP_PID 2>/dev/null || true" EXIT

# Wait for startup
sleep 3

# Warmup: 5s
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

# Measure: LOAD_DURATION seconds of blocking load
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

# Stop app (triggers profile write)
kill "$APP_PID" 2>/dev/null || true
wait "$APP_PID" 2>/dev/null || true
trap - EXIT

# Find and process the isolate log
ISOLATE=$(ls "$LAB_ROOT/app"/isolate-*.log 2>/dev/null | head -1)
if [ -z "$ISOLATE" ]; then
  echo "ERROR: no isolate log found" >&2
  exit 1
fi

echo "=== V8 CPU profile: /sync-cpu?ms=${SYNC_MS}, concurrency=${CONCURRENCY}, duration=${LOAD_DURATION}s ==="
echo ""
node --prof-process "$ISOLATE" 2>/dev/null
