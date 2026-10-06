#!/usr/bin/env bash
# Start the stack once, run every scenario (or the ones given) back to back, then analyse.
# Usage: scripts/run-all.sh [scenario-name ...]   (names = scenarios/<name>.env)
# A scenario with RUNS=n (n > 1) in its env file runs n times back to back; each run goes to
# results/<scenario>/run-<i>/ and scripts/runs.py writes results/<scenario>/runs.txt.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
cd "$here"
if [ $# -gt 0 ]; then scs=("$@"); else scs=($(ls scenarios | sed 's/\.env$//' | sort)); fi

dc() { docker compose "$@"; }
now_ms() { date +%s%3N; }
admin() { dc exec -T nginx curl -s "http://downstream:9001/admin?$1"; }
# accept queue length (Recv-Q of the LISTEN socket) + accepted-but-open sockets, in the downstream netns
kstate() {
  dc exec -T kernel sh -c '
    q=$(ss -ltnH "sport = :8081" | awk "{print \$2}")
    u=$(ss -tnpH state established state close-wait "sport = :8081" | grep -c users: || true)
    n=$(ss -tnH state established state close-wait "sport = :8081" | wc -l)
    echo "$q $u $n"'
}

dc down -t 1 >/dev/null 2>&1 || true
rm -rf results; mkdir -p results/raw; chmod 777 results results/raw
dc build > results/build.log 2>&1
dc up -d >/dev/null 2>&1
for i in $(seq 60); do admin "" >/dev/null 2>&1 && break; sleep 1; done
for i in $(seq 60); do grep -q "^E " results/raw/bpf.log 2>/dev/null && break; sleep 1; done

{
  echo "\$ uname -r"; uname -r
  echo "\$ nproc"; nproc
  echo "\$ docker version --format '{{.Server.Version}}'"; docker version --format '{{.Server.Version}}'
  echo "\$ docker compose version"; docker compose version
  echo "\$ docker compose images"; dc images
  echo "\$ k6 version"; dc exec -T k6 k6 version
  echo "\$ nginx -v"; dc exec -T nginx nginx -v 2>&1
  echo "\$ grep '^FROM' Dockerfile"; grep '^FROM' Dockerfile
  echo "\$ bpftrace --version"; dc exec -T bpf bpftrace --version
  echo "\$ ss -V"; dc exec -T kernel ss -V
  echo "## downstream netns"
  echo "\$ sysctl net.core.somaxconn net.ipv4.tcp_max_syn_backlog"
  dc exec -T kernel sh -c 'cat /proc/sys/net/core/somaxconn /proc/sys/net/ipv4/tcp_max_syn_backlog'
  echo "\$ ss -ltn  (Send-Q of a LISTEN socket = its backlog)"
  dc exec -T kernel ss -ltn
  echo "## nginx upstream keepalive (none configured => one connection per upstream request)"
  echo "\$ grep -n keepalive nginx/*.conf"; grep -n keepalive nginx/*.conf || echo "(no match)"
} > results/00-environment.txt 2>&1

# one request = one connection check: a few requests through nginx, then look for leftover
# nginx<->downstream connections from both network namespaces
{
  ngx=$(dc ps -q nginx)
  echo "\$ curl through nginx x5"
  for i in 1 2 3 4 5; do dc exec -T nginx curl -s -o /dev/null -w '%{http_code} %{time_total}\n' http://localhost/api/light; done
  sleep 1
  echo "\$ ss -tna (nginx netns), connections to :8081"
  docker run --rm --network "container:$ngx" rate-limit-cannot-protect-probe:local ss -tnaoH "dport = :8081" || true
  echo "[end]"
  echo "\$ ss -tna (downstream netns), app port :8081"
  dc exec -T kernel ss -tnaopH "sport = :8081" || true
  echo "[end]"
  echo "\$ tail -5 access.log"
  tail -5 results/raw/access.log
  echo "\$ tail bpf.log (A = accept, C = close)"
  grep -E '^(A|C) ' results/raw/bpf.log | tail -10 || true
} > results/00-connection-check.txt 2>&1

runs_of() { ( RUNS=1; . "scenarios/$1.env"; echo "$RUNS" ); }

run_one() {
  local sc=$1 out=$2
  (
    set -a
    NGINX_CONF=base.conf INIT_QUERY=
    RATE=200 DURATION=25s SWITCH_AT=8 HEAVY_BEFORE=0 HEAVY_AFTER=0 ADMIN_QUERY= TENANT_MODE=0
    TENANT_RATE=40 EARLY_TENANTS=3 TOTAL_TENANTS=10 FINE=0 TENANT_PICK=random ARRIVAL=fixed THIN=10 RUNS=1
    . "scenarios/$sc.env"
    set +a
    mkdir -p "$out"; chmod 777 "$out"
    cp "scenarios/$sc.env" "nginx/$NGINX_CONF" "$out/"
    dc exec -T nginx sh -c "cp /confs/$NGINX_CONF /etc/nginx/nginx.conf && nginx -t && nginx -s reload" > "$out/nginx-reload.txt" 2>&1
    # init: drain=1 makes the downstream accept everything and answer at once, which empties
    # the accept queue left by the previous scenario; then reset to defaults + scenario values.
    {
      local t0 st
      t0=$(now_ms)
      echo "before drain (recvq inuse est+close-wait): $(kstate)"
      admin "drain=1"; echo
      for i in $(seq 100); do
        st=$(kstate)
        [ "$st" = "0 0 0" ] && break
        sleep 0.1
      done
      echo "after drain  (recvq inuse est+close-wait): $st  ($(( $(now_ms) - t0 )) ms)"
      admin "reset=1&$INIT_QUERY"; echo
      echo "after reset  (recvq inuse est+close-wait): $(kstate)"
      dc exec -T kernel ss -ltnH "sport = :8081"
    } > "$out/init.txt" 2>&1
    grep -n -E 'proxy_next_upstream|max_conns|proxy_read_timeout|proxy_http_version|Connection|keepalive|server downstream' \
      "nginx/$NGINX_CONF" > "$out/nginx-conf-check.txt" || true
    local t_start t_end
    t_start=$(now_ms)
    dc exec -T -e RATE="$RATE" -e DURATION="$DURATION" -e SWITCH_AT="$SWITCH_AT" \
      -e HEAVY_BEFORE="$HEAVY_BEFORE" -e HEAVY_AFTER="$HEAVY_AFTER" -e ADMIN_QUERY="$ADMIN_QUERY" \
      -e TENANT_MODE="$TENANT_MODE" -e TENANT_RATE="$TENANT_RATE" -e EARLY_TENANTS="$EARLY_TENANTS" \
      -e TOTAL_TENANTS="$TOTAL_TENANTS" -e TENANT_PICK="$TENANT_PICK" -e ARRIVAL="$ARRIVAL" -e THIN="$THIN" \
      k6 k6 run --quiet --out "csv=/${out}/k6.csv" --summary-export="/${out}/k6-summary.json" \
      /scripts/load.js > "$out/k6-stdout.txt" 2>&1 || echo "k6 exit $?" >> "$out/k6-stdout.txt"
    t_end=$(now_ms)
    printf 'T_START_MS=%s\nT_END_MS=%s\nSWITCH_AT=%s\nDURATION=%s\nFINE=%s\n' \
      "$t_start" "$t_end" "$SWITCH_AT" "${DURATION%s}" "$FINE" > "$out/meta.env"
  )
}

jobs=()
for sc in "${scs[@]}"; do
  n=$(runs_of "$sc")
  if [ "$n" -gt 1 ]; then
    for i in $(seq "$n"); do jobs+=("results/$sc/run-$i"); done
  else
    jobs+=("results/$sc")
  fi
done

t_all=$(now_ms)
: > results/timing.txt
for out in "${jobs[@]}"; do
  sc=${out#results/}; sc=${sc%%/*}
  echo "== $out"
  t1=$(now_ms)
  run_one "$sc" "$out"
  echo "$out $(( ($(now_ms) - t1) / 1000 )) s" >> results/timing.txt
done
t_all_end=$(now_ms)
echo "scenarios: ${scs[*]}" >> results/timing.txt
echo "scenario runs: ${#jobs[@]}" >> results/timing.txt
echo "wall time of the scenario loop (stack start excluded): $(( (t_all_end - t_all) / 1000 )) s" >> results/timing.txt

dc logs --no-color downstream > results/raw/downstream.log 2>&1
dc logs --no-color kernel bpf > results/raw/probes.log 2>&1
dc down -t 3 >/dev/null 2>&1

for out in "${jobs[@]}"; do
  . "$out/meta.env"
  python3 scripts/slice.py results/raw "$out" "$T_START_MS" "$T_END_MS"
  python3 scripts/analyze.py "$out" "$SWITCH_AT" "$DURATION" > "$out/summary.txt"
  if [ "$FINE" = 1 ]; then python3 scripts/onset.py "$out" "$SWITCH_AT" > "$out/onset.txt"; fi
done
for sc in "${scs[@]}"; do
  if [ "$(runs_of "$sc")" -gt 1 ]; then python3 scripts/runs.py "results/$sc" > "results/$sc/runs.txt"; fi
done
cat results/timing.txt
