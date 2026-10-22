#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
o=$here/results/somaxconn
tools=nginx-aq-tools
raised=65535
out=$o/somaxconn.txt
mkdir -p "$o"
: > "$out"

docker rm -f lab-app lab-ctl >/dev/null 2>&1 || true
docker run -d --name lab-ctl --privileged --pid host "$tools" sleep infinity >/dev/null
hostctl() { docker exec lab-ctl nsenter -t 1 -n sysctl "$@"; }
orig=$(hostctl -n net.core.somaxconn)
restore() {
  hostctl -w "net.core.somaxconn=$orig" >/dev/null 2>&1 || true
  docker rm -f lab-app lab-ctl >/dev/null 2>&1 || true
}
trap restore EXIT

measure() {
  local name=$1; shift
  docker run -d --name lab-app "$@" -v "$here/app":/app:ro -e BACKLOG=100 -e LOG=/dev/null \
    node:22.22.3-bookworm-slim node /app/server.js >/dev/null
  local pid
  pid=$(docker inspect -f '{{.State.Pid}}' lab-app)
  for _ in $(seq 50); do
    docker exec lab-ctl nsenter -t "$pid" -n ss -Hltn 'sport = :8081' | grep -q LISTEN && break
    sleep 0.2
  done
  {
    echo "## $name"
    echo "docker run options: $*"
    echo "host net.core.somaxconn: $(hostctl -n net.core.somaxconn)"
    echo "container net.core.somaxconn: $(docker exec lab-ctl nsenter -t "$pid" -n sysctl -n net.core.somaxconn)"
    echo "app stderr: $(docker logs lab-app 2>&1)"
    echo "ss -Hltn 'sport = :8081' in the container network namespace:"
    docker exec lab-ctl nsenter -t "$pid" -n ss -Hltn 'sport = :8081'
    echo
  } >> "$out"
  docker rm -f lab-app >/dev/null
}

measure host-default
hostctl -w "net.core.somaxconn=$raised" >/dev/null
measure host-raised
hostctl -w "net.core.somaxconn=$orig" >/dev/null
measure container-raised --sysctl "net.core.somaxconn=$raised"
echo "host net.core.somaxconn after restore: $(hostctl -n net.core.somaxconn)" >> "$out"
