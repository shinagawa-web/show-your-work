#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
if [ $# -gt 0 ]; then scs=("$@"); else scs=($(ls scenarios | sed 's/\.env$//' | sort)); fi

t_job=$(date +%s%3N)
dc() { docker compose "$@"; }
now_ms() { date +%s%3N; }
admin() { dc exec -T nginx curl -s "http://app:9001/admin?$1"; }
kstate() {
  dc exec -T kernel sh -c '
    q=$(ss -ltnH "sport = :8081" | awk "{print \$2}")
    u=$(ss -tnpH state established state close-wait "sport = :8081" | grep -c users: || true)
    n=$(ss -tnH state established state close-wait "sport = :8081" | wc -l)
    echo "$q $u $n"'
}
K6_VARS=(ITER_RATE DURATION SWITCH_AT TENANT_RATE TENANTS_BEFORE TENANTS_AFTER HEAVY_AFTER ADMIN_QUERY K6_TIMEOUT)

dc down -t 1 >/dev/null 2>&1 || true
rm -rf results; mkdir -p results/raw; chmod 777 results results/raw
dc build > results/build.log 2>&1
dc up -d >/dev/null 2>&1
for i in $(seq 60); do admin "" >/dev/null 2>&1 && break; sleep 1; done
bpf_ready=
for i in $(seq 60); do grep -q "^E " results/raw/bpf.log 2>/dev/null && bpf_ready=1 && break; sleep 1; done
if [ -z "$bpf_ready" ]; then
  echo "bpftrace did not start within 60 s: no 'E ' line in results/raw/bpf.log" >&2
  dc logs --no-color bpf > results/raw/bpf-start.log 2>&1 || true
  dc down -t 1 >/dev/null 2>&1 || true
  exit 1
fi
t_ready=$(now_ms)

{
  echo "\$ uname -r"; uname -r
  echo "\$ nproc"; nproc
  echo "\$ docker version --format '{{.Server.Version}}'"; docker version --format '{{.Server.Version}}'
  echo "\$ docker compose version"; docker compose version
  echo "\$ docker compose images"; dc images
  echo "\$ k6 version"; dc exec -T k6 k6 version
  echo "\$ nginx -V"; dc exec -T nginx nginx -V 2>&1
  echo "\$ grep '^FROM' Dockerfile"; grep '^FROM' Dockerfile
  echo "\$ bpftrace --version"; dc exec -T bpf bpftrace --version
  echo "\$ ss -V"; dc exec -T kernel ss -V
  echo "## app netns"
  echo "\$ cat /proc/sys/net/core/somaxconn /proc/sys/net/ipv4/tcp_max_syn_backlog"
  dc exec -T kernel sh -c 'cat /proc/sys/net/core/somaxconn /proc/sys/net/ipv4/tcp_max_syn_backlog'
  echo "\$ ss -ltn  (Send-Q of a LISTEN socket = its backlog)"
  dc exec -T kernel ss -ltn
  echo "## nginx netns"
  echo "\$ cat /proc/sys/net/ipv4/ip_local_port_range"
  dc exec -T nginx cat /proc/sys/net/ipv4/ip_local_port_range
} > results/00-environment.txt 2>&1

dc exec -T nginx nginx -T > results/00-base-nginx-T.txt 2> results/00-base-nginx-T-stderr.txt

{
  ngx=$(dc ps -q nginx)
  echo "\$ nginx -T (base.conf)"
  dc exec -T nginx nginx -T 2>&1
  echo "\$ curl through nginx x5"
  for i in 1 2 3 4 5; do dc exec -T nginx curl -s -o /dev/null -H 'X-Tenant: check' -w '%{http_code} %{time_total}\n' http://localhost/api/light; done
  sleep 1
  echo "\$ tail -5 app_access.log  (start proc_ms path request_id tenant proto connection)"
  tail -5 results/raw/app_access.log
  echo "\$ tail -5 access.log"
  tail -5 results/raw/access.log
  echo "\$ ss -tnao (nginx netns), connections to :8081"
  docker run --rm --network "container:$ngx" rate-limit-cannot-protect-probe:local ss -tnaoH "dport = :8081" || true
  echo "[end]"
  echo "\$ ss -tnaop (app netns), app port :8081"
  dc exec -T kernel ss -tnaopH "sport = :8081" || true
  echo "[end]"
  echo "\$ bpf.log A / C lines of these requests"
  grep -E '^(A|C) ' results/raw/bpf.log | tail -10 || true
} > results/00-connection-check.txt 2>&1

run_one() {
  local sc=$1 out=results/$1
  (
    set -a
    NGINX_CONF=base.conf
    ITER_RATE=600 DURATION=25s SWITCH_AT=8 TENANT_RATE=20 TENANTS_BEFORE=10 TENANTS_AFTER=10
    HEAVY_AFTER=0 ADMIN_QUERY= K6_TIMEOUT=60s
    . "scenarios/$sc.env"
    set +a
    mkdir -p "$out"; chmod 777 "$out"
    cp "scenarios/$sc.env" "$out/scenario.env"
    dc exec -T nginx sh -c "cp /confs/$NGINX_CONF /etc/nginx/nginx.conf && nginx -t && nginx -s reload" > "$out/nginx-reload.txt" 2>&1
    sleep 1
    dc exec -T nginx nginx -T > "$out/nginx-T.txt" 2> "$out/nginx-T-stderr.txt"
    {
      echo "before reset (recvq inuse est+close-wait): $(kstate)"
      echo "\$ GET /admin?reset=1"; admin "reset=1"; echo
      echo "after reset  (recvq inuse est+close-wait): $(kstate)"
      dc exec -T kernel ss -ltnH "sport = :8081"
    } > "$out/init.txt" 2>&1
    local envargs=() v
    : > "$out/k6-env.txt"
    for v in "${K6_VARS[@]}"; do
      envargs+=(-e "$v=${!v}")
      echo "$v=${!v}" >> "$out/k6-env.txt"
    done
    local t_start t_end t_drained st drained
    t_start=$(now_ms)
    dc exec -T "${envargs[@]}" k6 k6 run --quiet --log-format raw --out "csv=/${out}/k6.csv" \
      --summary-export="/${out}/k6-summary.json" /scripts/load.js > "$out/k6-stdout.txt" 2>&1 \
      || echo "k6 exit $?" >> "$out/k6-stdout.txt"
    t_end=$(now_ms)
    drained=0
    for i in $(seq 300); do
      st=$(kstate)
      [ "$st" = "0 0 0" ] && drained=1 && break
      sleep 0.2
    done
    t_drained=$(now_ms)
    echo "after k6 (recvq inuse est+close-wait): $st  ($(( t_drained - t_end )) ms after k6 exit)" >> "$out/init.txt"
    if [ "$drained" = 0 ]; then
      echo "DRAIN TIMEOUT: $sc app sockets still $st (recvq inuse est+close-wait) 60 s after k6 exit" | tee -a "$out/init.txt" >&2
    fi
    sleep 0.3
    printf 'T_START_MS=%s\nT_END_MS=%s\nT_DRAINED_MS=%s\nDRAINED=%s\nDRAIN_LAST=%s\nSWITCH_AT=%s\nDURATION=%s\n' \
      "$t_start" "$t_end" "$t_drained" "$drained" "$st" "$SWITCH_AT" "${DURATION%s}" > "$out/meta.env"
  )
}

t_all=$(now_ms)
: > results/timing.txt
for sc in "${scs[@]}"; do
  echo "== $sc"
  t1=$(now_ms)
  run_one "$sc"
  echo "$sc $(( ($(now_ms) - t1) / 1000 )) s" >> results/timing.txt
done
t_all_end=$(now_ms)

dc logs --no-color app > results/raw/app.log 2>&1
dc logs --no-color kernel bpf > results/raw/probes.log 2>&1
dc down -t 3 >/dev/null 2>&1

for sc in "${scs[@]}"; do
  python3 scripts/slice.py results/raw "results/$sc"
done
python3 scripts/analyze.py results "${scs[@]}" > results/checks.txt
t_job_end=$(now_ms)
{
  echo "scenarios: ${scs[*]}"
  echo "stack build and start: $(( (t_ready - t_job) / 1000 )) s"
  echo "scenario loop: $(( (t_all_end - t_all) / 1000 )) s"
  echo "total (build, start, scenarios, analysis): $(( (t_job_end - t_job) / 1000 )) s"
} >> results/timing.txt
cat results/timing.txt
