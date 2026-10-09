#!/bin/sh
set -e

WDIR="$(cd "$(dirname "$0")" && pwd)"
RATE=${RATE:-200}
SYNC_MS=${SYNC_MS:-30000}
DURATION=${DURATION:-60}
TMP_RESULTS="/tmp/oom-lima-timeseries.tsv"

docker rm -f go-queue-oom 2>/dev/null || true

echo "--- building ---"
docker build -t go-queue-oom "$WDIR"

echo "--- starting (mem_limit=128m) ---"
docker run -d --name go-queue-oom --memory=128m -p 3000:3000 go-queue-oom

echo "--- waiting for ready ---"
for i in $(seq 1 30); do
  if curl -sf http://localhost:3000/light > /dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "--- collecting time series (RATE=$RATE req/s, SYNC_MS=${SYNC_MS}ms, DURATION=${DURATION}s) ---"

docker logs -f go-queue-oom 2>&1 | grep --line-buffered '^ts=' > "$TMP_RESULTS" &
LOGS_PID=$!

python3 - <<EOF
import socket, time, threading

RATE = $RATE
SYNC_MS = $SYNC_MS
DURATION = $DURATION
interval = 1.0 / RATE
sent = 0
start = time.time()

def fire():
    try:
        s = socket.create_connection(("localhost", 3000), timeout=2)
        req = f"GET /sync-io?ms={SYNC_MS} HTTP/1.0\r\nHost: localhost\r\n\r\n"
        s.sendall(req.encode())
        s.settimeout(1)
        try:
            s.recv(1)
        except Exception:
            pass
        s.close()
    except Exception:
        pass

while time.time() - start < DURATION:
    threading.Thread(target=fire, daemon=True).start()
    sent += 1
    time.sleep(interval)

import sys
print(f"sent={sent}", file=sys.stderr)
time.sleep(3)
EOF

sleep 3
kill $LOGS_PID 2>/dev/null || true

EXIT_CODE=$(docker inspect --format='{{.State.ExitCode}}' go-queue-oom 2>/dev/null || echo "unknown")
OOM=$(docker inspect --format='{{.State.OOMKilled}}' go-queue-oom 2>/dev/null || echo "unknown")
echo "exit_code=$EXIT_CODE oom_killed=$OOM"

docker rm -f go-queue-oom 2>/dev/null || true

echo "--- results ---"
cat "$TMP_RESULTS"
