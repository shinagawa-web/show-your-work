#!/usr/bin/env bash
set -uo pipefail
cond=${1:?condition}
cd /lab
o=results/$cond
r=/tmp/run
rm -rf "$o" "$r"; mkdir -p "$o" "$r"

duration_s=60
users=16
think_ms=10
burst_size=0
burst_min_s=0
burst_max_s=0
conf=conf/nginx.conf

case "$cond" in
  baseline) ;;
  idle-close) duration_s=120; burst_size=64; burst_min_s=5.99; burst_max_s=6.02 ;;
  idle-close-nginx-shorter) duration_s=120; burst_size=64; burst_min_s=5.99; burst_max_s=6.02; conf=conf/nginx-keepalive-timeout.conf ;;
  *) echo "unknown condition: $cond" >&2; exit 2 ;;
esac

sed "s|/lab/results/run/|/lab/$o/|g" "$conf" > "$r/nginx.conf"
cp "$r/nginx.conf" "$o/nginx.conf"

start_backend() {
  PORT=$1 REQUEST_LOG=$o/backend-requests.log \
    node backend/server.js 2>> "$o/backend-stderr.log" &
  echo $! > "$r/backend-$1.pid"
  for _ in $(seq 1 100); do
    ss -Hltn "sport = :$1" | grep -q . && return 0
    sleep 0.05
  done
  echo "backend $1 did not listen" >&2; return 1
}
stop_backend() {
  local pid; pid=$(cat "$r/backend-$1.pid")
  kill -TERM "$pid"
  wait "$pid" 2>/dev/null
}

start_backend 8081 && start_backend 8082 || exit 1
nginx -c "$r/nginx.conf" || exit 1
sleep 0.5

tcpdump -i lo -nn -s 128 -w "$o/capture.pcap" \
  '(tcp port 8081 or tcp port 8082) and ((tcp[tcpflags] & (tcp-syn|tcp-fin|tcp-rst) != 0) or ((dst port 8081 or dst port 8082) and (tcp[tcpflags] & tcp-push != 0)))' \
  2> "$o/tcpdump.log" &
tcpdump_pid=$!
sleep 1

DURATION_S=$duration_s USERS=$users THINK_MS=$think_ms BURST_SIZE=$burst_size BURST_MIN_S=$burst_min_s BURST_MAX_S=$burst_max_s \
  OUT=$o/client.log BURST_OUT=$o/bursts.log node load/load.js 2> "$o/load-stderr.log"

sleep 1
kill "$tcpdump_pid"; wait "$tcpdump_pid" 2>/dev/null
nginx -c "$r/nginx.conf" -s quit
sleep 0.5
for p in 8081 8082; do stop_backend "$p"; done
{
  echo "condition: $cond"
  echo "load: users=$users think_ms=$think_ms burst_size=$burst_size burst_interval_s=$burst_min_s-$burst_max_s"
  echo "nginx config: $conf"
  nginx -v 2>&1
  echo "node $(node -v)"
} > "$o/settings.txt"
