#!/bin/sh
set -e

WDIR="$(cd "$(dirname "$0")" && pwd)"
RATE=${RATE:-200}
SYNC_MS=${SYNC_MS:-30000}
DURATION=${DURATION:-60}
TMP_RESULTS="/tmp/latency-timeseries.tsv"

docker rm -f go-queue-lat 2>/dev/null || true

echo "--- building ---"
docker build -t go-queue-lat "$WDIR"

echo "--- starting (no mem_limit) ---"
docker run -d --name go-queue-lat -p 3000:3000 go-queue-lat

echo "--- waiting for ready ---"
for i in $(seq 1 30); do
  if curl -sf http://localhost:3000/light > /dev/null 2>&1; then break; fi
  sleep 1
done

echo "--- running (RATE=$RATE req/s, SYNC_MS=${SYNC_MS}ms, DURATION=${DURATION}s) ---"

docker logs -f go-queue-lat 2>&1 | grep --line-buffered '^ts=' > "$TMP_RESULTS" &
LOGS_PID=$!

python3 - <<'PYEOF'
import socket, time, threading, statistics, os

RATE     = int(os.environ.get('RATE', 200))
SYNC_MS  = int(os.environ.get('SYNC_MS', 30000))
DURATION = int(os.environ.get('DURATION', 60))

def raw_get(path, timeout=2):
    s = socket.create_connection(("localhost", 3000), timeout=2)
    s.sendall(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
    s.settimeout(timeout)
    try:
        s.recv(256)
    except Exception:
        pass
    s.close()

def probe(path, n=20):
    times = []
    def one():
        t0 = time.time()
        try:
            s = socket.create_connection(("localhost", 3000), timeout=5)
            s.sendall(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
            buf = b""
            s.settimeout(5)
            while b"\r\n\r\n" not in buf:
                chunk = s.recv(256)
                if not chunk:
                    break
                buf += chunk
            s.close()
            times.append((time.time() - t0) * 1000)
        except Exception:
            pass
    ts = [threading.Thread(target=one) for _ in range(n)]
    for t in ts: t.start()
    for t in ts: t.join()
    if not times:
        return None
    times.sort()
    return {
        "p50": round(times[len(times)//2], 1),
        "p99": round(times[min(len(times)-1, int(len(times)*0.99))], 1),
        "n": len(times),
    }

start = time.time()
interval = 1.0 / RATE
next_probe = start + 5

def accumulator():
    t = start
    while time.time() - start < DURATION:
        threading.Thread(target=raw_get,
            args=(f"/sync-io?ms={SYNC_MS}", 1),
            daemon=True).start()
        t += interval
        sleep = t - time.time()
        if sleep > 0:
            time.sleep(sleep)

threading.Thread(target=accumulator, daemon=True).start()

while time.time() - start < DURATION:
    now = time.time()
    if now >= next_probe:
        ts = round(now - start)
        light = probe("/light")
        short = probe(f"/sync-io?ms=200")
        if light and short:
            print(f"probe ts={ts} light_p99={light['p99']}ms short_p99={short['p99']}ms", flush=True)
        next_probe += 5
    time.sleep(0.1)

time.sleep(3)
PYEOF

sleep 3
kill $LOGS_PID 2>/dev/null || true
docker rm -f go-queue-lat 2>/dev/null || true

echo "--- goroutine/mem time series ---"
cat "$TMP_RESULTS"
