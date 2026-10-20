#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
here=$PWD
if [ $# -gt 0 ]; then conds=("$@"); else conds=($(ls conditions | sed 's/\.env$//' | sort)); fi
now_ms() { date +%s%3N; }
NGINX_IMAGE=nginx:1.30.6
RATE=500
DURATION_SEC=120
PAGES=100
USERS=200

load_cond() {
  CACHE_VALID=10s NGINX_EXTRA= APP_PRIVATE=0 APP_VARY=0 LOGIN_SHARE=0.10 FROM=
  . "conditions/$1.env"
}
render() {
  local t
  t=$(<nginx/default.conf.template)
  t=${t//@CACHE_VALID@/"$CACHE_VALID"}
  t=${t//@NGINX_EXTRA@/"$NGINX_EXTRA"}
  printf '%s\n' "$t" > "$1"
}
write_settings() {
  printf 'APP_PAGES=%s\nAPP_PRIVATE=%s\nAPP_VARY=%s\n' "$PAGES" "$APP_PRIVATE" "$APP_VARY" > "$1/app.env"
  printf 'DURATION=%ss\nLOGIN_SHARE=%s\nPAGES=%s\nRATE=%s\nUSERS=%s\n' "$DURATION_SEC" "$LOGIN_SHARE" "$PAGES" "$RATE" "$USERS" > "$1/k6.env"
}

all=()
for c in "${conds[@]}"; do
  [ -f "conditions/$c.env" ] || { echo "unknown condition $c" >&2; exit 2; }
  load_cond "$c"
  all+=("$c")
  if [ -n "$FROM" ]; then
    [ -f "conditions/$FROM.env" ] || { echo "unknown FROM $FROM in $c" >&2; exit 2; }
    all+=("$FROM")
  fi
done

t_job=$(now_ms)
rm -rf results
mkdir -p results/settings
for img in "$NGINX_IMAGE" python:3.13.15-slim grafana/k6:2.3.0; do docker pull -q "$img"; done > results/pull.log 2>&1
t_pulled=$(now_ms)

for c in $(printf '%s\n' "${all[@]}" | sort -u); do
  (
    load_cond "$c"
    d=results/settings/$c
    mkdir -p "$d/conf.d"
    render "$d/conf.d/default.conf"
    write_settings "$d"
    docker run --rm --add-host app:127.0.0.1 --tmpfs /results -v "$here/$d/conf.d:/etc/nginx/conf.d:ro" --entrypoint nginx "$NGINX_IMAGE" -T > "$d/nginx-T.txt" 2> "$d/nginx-T-stderr.txt"
  ) &
done
wait

fail() { echo "failed: $2" > "$1/run-status.txt"; return 1; }

steps() {
  local c=$1 out=$2 i ready=
  dc up -d > "$out/compose-up.log" 2>&1 || { fail "$out" "docker compose up"; return 1; }
  for i in $(seq 100); do
    if dc exec -T nginx curl -sf -o /dev/null "http://localhost/login?user=ready" 2>/dev/null; then ready=1; break; fi
    sleep 0.2
  done
  [ -n "$ready" ] || { fail "$out" "nginx did not answer /login after 100 tries"; return 1; }
  dc exec -T nginx nginx -T > "$out/nginx-T.txt" 2> "$out/nginx-T-stderr.txt" || { fail "$out" "nginx -T"; return 1; }
  dc exec -T app sh -c 'env | grep ^APP_ | sort' > "$out/app-env.txt" || { fail "$out" "app env"; return 1; }
  local a0 a1 t0 t1 rc=0 envargs=() line
  while IFS= read -r line; do envargs+=(-e "$line"); done < "$here/results/settings/$c/k6.env"
  a0=$(grep -c ' /items/' "$out/app.log" || true)
  t0=$(now_ms)
  dc exec -T k6 k6 run --quiet --log-format raw --console-output /results/k6-requests.log \
    "${envargs[@]}" -e OUT=/results/k6-summary.json \
    /scripts/load.js > "$out/k6-stdout.txt" 2>&1 || rc=$?
  t1=$(now_ms)
  a1=$(grep -c ' /items/' "$out/app.log" || true)
  printf 'APP_ITEMS_BEFORE=%s\nAPP_ITEMS_AFTER=%s\nK6_START_MS=%s\nK6_END_MS=%s\n' "$a0" "$a1" "$t0" "$t1" > "$out/meta.env"
  [ "$rc" = 0 ] || { fail "$out" "k6 exit $rc"; return 1; }
  dc exec -T nginx sh -c 'find /var/cache/nginx/pages -type f | wc -l' > "$out/cache-files.txt" || { fail "$out" "count cache files"; return 1; }
  dc exec -T nginx sh -s < scripts/curl-check.sh > "$out/curl-check.txt" 2>&1 || { fail "$out" "curl check"; return 1; }
  echo ok > "$out/run-status.txt"
}

run_one() {
  local c=$1 out=$here/results/$1
  load_cond "$c"
  mkdir -p "$out/conf.d"
  chmod 777 "$out"
  echo "failed: did not finish" > "$out/run-status.txt"
  render "$out/conf.d/default.conf"
  export OUT=$out APP_PRIVATE APP_VARY APP_PAGES=$PAGES
  local p="ncso-${c%%-*}"
  dc() { docker compose -p "$p" "$@"; }
  dc down -v -t 1 > /dev/null 2>&1 || true
  steps "$c" "$out" || true
  dc logs --no-color > "$out/compose.log" 2>&1 || true
  dc down -v -t 1 > /dev/null 2>&1 || true
}

t_run=$(now_ms)
pids=()
for c in "${conds[@]}"; do
  run_one "$c" > "results/$c.log" 2>&1 &
  pids+=($!)
done
for pid in "${pids[@]}"; do wait "$pid" || true; done
t_run_end=$(now_ms)

{
  echo "\$ uname -r"; uname -r
  echo "\$ nproc"; nproc
  echo "\$ docker version --format '{{.Server.Version}}'"; docker version --format '{{.Server.Version}}'
  echo "\$ docker compose version"; docker compose version
  echo "\$ docker run --rm --entrypoint nginx $NGINX_IMAGE -V"; docker run --rm --entrypoint nginx "$NGINX_IMAGE" -V 2>&1
  echo "\$ docker run --rm grafana/k6:2.3.0 version"; docker run --rm grafana/k6:2.3.0 version
} > results/environment.txt 2>&1 || true

set +e
python3 scripts/analyze.py results "${conds[@]}"
rc=$?
set -e
t_end=$(now_ms)
{
  echo "conditions: ${conds[*]}"
  echo "image pull: $(( (t_pulled - t_job) / 1000 )) s"
  echo "conditions in parallel: $(( (t_run_end - t_run) / 1000 )) s"
  echo "total: $(( (t_end - t_job) / 1000 )) s"
} > results/timing.txt
cat results/timing.txt
exit $rc
