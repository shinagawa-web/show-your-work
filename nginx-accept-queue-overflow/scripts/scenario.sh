#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
cond=$1 rep=$2
row=$(awk -F'\t' -v c="$cond" '$1 == c' "$here/conditions.tsv")
[ -n "$row" ] || { echo "unknown condition: $cond" >&2; exit 2; }
IFS=$'\t' read -r _ _ backlog pause ct linear abort <<< "$row"
rate=200 duration=15 stop_at=5
o=$here/results/$cond/rep-$rep
tools=nginx-aq-tools
net=nginx-aq-net
names=(lab-load lab-ctl lab-front lab-app)

cleanup() {
  docker rm -f "${names[@]}" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
}
now() { date +%s.%N; }

cleanup
mkdir -p "$o/extra"
chmod 777 "$o"
echo "proxy_connect_timeout $ct;" > "$o/extra/connect-timeout.conf"
printf 'condition=%s\nrep=%s\nbacklog=%s\npause_s=%s\nproxy_connect_timeout=%s\nsyn_linear_timeouts=%s\nabort_on_overflow=%s\nrate=%s\nduration_s=%s\nstop_at_s=%s\n' \
  "$cond" "$rep" "$backlog" "$pause" "$ct" "$linear" "$abort" "$rate" "$duration" "$stop_at" > "$o/condition.env"
docker network create "$net" >/dev/null

docker run -d --name lab-app --network "$net" --sysctl "net.ipv4.tcp_abort_on_overflow=$abort" \
  -v "$here/app":/app:ro -v "$o":/results -e BACKLOG="$backlog" -e IDLE_MS=75000 \
  node:22.22.3-bookworm-slim node /app/server.js >/dev/null
docker run -d --name lab-front --network "$net" --sysctl "net.ipv4.tcp_syn_linear_timeouts=$linear" \
  -v "$here/conf/nginx.conf":/etc/nginx/nginx.conf:ro -v "$o/extra":/etc/nginx/extra:ro -v "$o":/results \
  nginx:1.30.6 >/dev/null
docker run -d --name lab-ctl --privileged --pid host \
  -v "$o":/results -v "$here/scripts":/lab:ro "$tools" sleep infinity >/dev/null
app_pid=$(docker inspect -f '{{.State.Pid}}' lab-app)
front_pid=$(docker inspect -f '{{.State.Pid}}' lab-front)
ctl() { docker exec lab-ctl "$@"; }

for _ in $(seq 50); do
  ctl nsenter -t "$app_pid" -n ss -Hltn 'sport = :8081' | grep -q LISTEN \
    && ctl nsenter -t "$front_pid" -n ss -Hltn 'sport = :8080' | grep -q LISTEN && break
  sleep 0.2
done
ctl nsenter -t "$front_pid" -n ss -Hltn 'sport = :8080' | grep -q LISTEN || { docker logs lab-front 2>&1 | tail -5; exit 1; }
ctl nsenter -t "$app_pid" -n ss -Hltn 'sport = :8081' | grep -q LISTEN || { docker logs lab-app 2>&1 | tail -5; exit 1; }

ctl nsenter -t "$app_pid" -n sh -c 'sysctl net.core.somaxconn net.ipv4.tcp_abort_on_overflow; ss -Hltn "sport = :8081"' \
  > "$o/config-app.txt" 2>&1
ctl nsenter -t "$front_pid" -n sysctl net.core.somaxconn net.ipv4.tcp_syn_linear_timeouts net.ipv4.tcp_syn_retries \
  > "$o/config-front.txt" 2>&1
docker exec lab-front nginx -T > "$o/nginx-T.txt" 2>&1
docker exec lab-front nginx -v >> "$o/nginx-T.txt" 2>&1

filter='tcp port 8081 and tcp[tcpflags] & (tcp-syn|tcp-rst) != 0'
for side in front app; do
  pid=$app_pid; [ "$side" = front ] && pid=$front_pid
  docker exec -d lab-ctl sh -c "echo \$\$ > /tmp/cap-$side.pid; exec nsenter -t $pid -n tcpdump -i eth0 -s 96 -U -Z root -w /results/$side.pcap '$filter'"
done
docker exec -d lab-ctl sh -c "echo \$\$ > /tmp/sampler.pid; while :; do echo \"\$(date +%s.%N) \$(nsenter -t $app_pid -n ss -Hltn 'sport = :8081')\"; sleep 0.05; done > /results/ss-listen.txt"
sleep 1

docker run -d --name lab-load --network "$net" -v "$o":/results "$tools" \
  sh -c "echo 'GET http://lab-front:8080/' | vegeta attack -rate=$rate/s -duration=${duration}s -timeout=90s > /results/vegeta.bin" >/dev/null
echo "load-start $(now)" > "$o/timeline.txt"
sleep "$stop_at"
ctl /lab/pause.sh "$app_pid" "$front_pid" "$pause"

docker wait lab-load >/dev/null
echo "load-end $(now)" >> "$o/timeline.txt"
sleep 1
ctl nsenter -t "$app_pid" -n nstat -asz TcpExtListenOverflows TcpExtListenDrops > "$o/nstat-after.txt"
ctl sh -c 'kill "$(cat /tmp/sampler.pid)"; kill -INT "$(cat /tmp/cap-front.pid)" "$(cat /tmp/cap-app.pid)"' || true
sleep 1
docker logs lab-app > "$o/app-stderr.log" 2>&1
docker logs lab-front > "$o/nginx-stdout.log" 2>&1
docker run --rm -v "$o":/results "$tools" vegeta report /results/vegeta.bin > "$o/vegeta-report.txt"
cleanup
