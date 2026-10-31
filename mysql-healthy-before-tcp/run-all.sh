#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
mapfile -t all < <(awk -F'\t' 'NR > 1 { print $1 }' jobs.tsv)
if [ $# -gt 0 ]; then jobs=("$@"); else jobs=("${all[@]}"); fi
for j in "${jobs[@]}"; do
  case " ${all[*]} " in *" $j "*) ;; *) echo "unknown job: $j (one of: ${all[*]})" >&2; exit 2 ;; esac
done
export TZ=UTC
RUNS=${RUNS:-3}
PARALLEL=${PARALLEL:-2}
results=$here/results
rm -rf "$results"; mkdir -p "$results"
start_all=$(date +%s)

docker pull -q mysql:9.7.4 > "$results/pull.log" 2>&1
{
  echo "\$ uname -srm"; uname -srm
  echo "\$ nproc"; nproc
  echo "\$ docker version --format '{{.Server.Version}}'"; docker version --format '{{.Server.Version}}'
  echo "\$ docker compose version"; docker compose version
  echo "\$ docker image inspect mysql:9.7.4 --format '{{json .RepoDigests}}'"; docker image inspect mysql:9.7.4 --format '{{json .RepoDigests}}'
  echo "\$ docker run --rm --entrypoint mysqld mysql:9.7.4 --version"; docker run --rm --entrypoint mysqld mysql:9.7.4 --version
  echo "\$ docker run --rm --entrypoint mysql mysql:9.7.4 --version"; docker run --rm --entrypoint mysql mysql:9.7.4 --version
  echo "\$ cat /etc/docker/daemon.json"; cat /etc/docker/daemon.json 2>&1
  echo "\$ ps -o args= -C dockerd"; ps -o args= -C dockerd
} > "$results/environment.txt" 2>&1
docker info > "$results/docker-info.txt" 2>&1
printf 'job\tseconds\n' > "$results/timing.tsv"

now() { date +%s.%N; }

wait_log() {
  local ctr=$1 pattern=$2 limit=$3 i
  for i in $(seq $((limit * 10))); do
    docker logs "$ctr" 2>&1 | grep -q -- "$pattern" && return 0
    sleep 0.1
  done
  return 1
}

HC_CHECKS=(
  "mysqladmin ping -h localhost"
  "mysqladmin ping"
  "mysqladmin ping -h 127.0.0.1"
  "mysqladmin ping --protocol=TCP -h 127.0.0.1"
)

check_phase() {
  local ctr=$1 hc=$2 port=$3 phase=$4 out=$5 i t rc
  for i in "${!HC_CHECKS[@]}"; do
    t=$(now)
    timeout 20 docker exec "$ctr" sh -c "${HC_CHECKS[$i]}" > "$out/$phase-$i.stdout" 2> "$out/$phase-$i.stderr"
    rc=$?
    printf '%s\t%s\t%s\t%s\t%s\n' "$phase" "$i" "${HC_CHECKS[$i]}" "$t" "$rc" >> "$out/checks.tsv"
  done
  t=$(now)
  timeout 10 docker exec "$hc" bash -c "exec 3<>/dev/tcp/127.0.0.1/$port && timeout 3 cat <&3 | wc -c" > "$out/$phase-tcp.stdout" 2> "$out/$phase-tcp.stderr"
  rc=$?
  printf '%s\t%s\t%s\t%s\t%s\n' "$phase" tcp "host: bash /dev/tcp/127.0.0.1/$port, then count bytes until EOF" "$t" "$rc" >> "$out/checks.tsv"
}

run_checks() {
  local ctr=$1 hc=$2 port=$3 out=$4
  mkdir -p "$out"
  printf 'phase\tindex\tcommand\tt_start\texit_code\n' > "$out/checks.tsv"
  if wait_log "$ctr" "/docker-entrypoint-initdb.d/10-sleep.sh" 180; then
    check_phase "$ctr" "$hc" "$port" during_sleep "$out"
  else
    echo "the init script did not start within 180 s" >> "$out/invalid.txt"
  fi
  if wait_log "$ctr" "port: 3306 " 180; then
    check_phase "$ctr" "$hc" "$port" after_real_server "$out"
  else
    echo "the real server did not log port: 3306 within 180 s" >> "$out/invalid.txt"
  fi
}

run_once() {
  local c=$1 p=$2 port=$3 out=$4 with_checks=$5 ctr hc ev1 ev2 pt ph pp=
  mkdir -p "$out"
  dc() { docker compose -p "$p" "${files[@]}" "$@"; }
  docker events --since "$(now)" --filter "label=com.docker.compose.project=$p" --format '{{json .}}' > "$out/events.jsonl" 2> "$out/events.err" &
  ev1=$!
  docker events --since "$(now)" --filter event=health_status --filter "label=com.docker.compose.project=$p" > "$out/health_status.txt" 2>> "$out/events.err" &
  ev2=$!
  sleep 0.5
  dc --profile test up -d hostclient > "$out/compose-up.log" 2>&1
  hc=$(dc --profile test ps -q hostclient)
  dc up -d mysql >> "$out/compose-up.log" 2>&1
  ctr=$(dc ps -a -q mysql)
  ( dc --profile test up -d tester > "$out/compose-up-tester.log" 2>&1
    echo "t_tester_up_returned=$(now)" > "$out/net-times.env"
    docker wait "$(dc --profile test ps -a -q tester)" > /dev/null 2>&1
    docker logs "$(dc --profile test ps -a -q tester)" > "$out/net.tsv" 2> "$out/net.err" ) &
  pt=$!
  ( dc up -d --wait --wait-timeout 180 mysql > "$out/compose-up-wait.log" 2>&1
    echo "rc=$? t_up_wait_returned=$(now)" > "$out/host-times.env"
    docker exec "$hc" bash /client.sh 127.0.0.1 "$port" "$CLIENT_MODE" > "$out/host.tsv" 2> "$out/host.err" ) &
  ph=$!
  if [ "$with_checks" = 1 ]; then run_checks "$ctr" "$hc" "$port" "$out/healthchecks" & pp=$!; fi
  wait $pt $ph $pp
  wait_log "$ctr" "port: 3306 " 180 || echo "the real server did not log port: 3306 within 180 s" >> "$out/invalid.txt"
  sleep 1
  kill $ev1 $ev2 2> /dev/null; wait $ev1 $ev2 2> /dev/null
  docker logs "$ctr" > "$out/mysql.log" 2>&1
  docker inspect "$ctr" > "$out/inspect.json" 2>&1
  ps -eo args | grep '[d]ocker-proxy' | grep -- "-host-port $port " > "$out/docker-proxy.txt"
  dc --profile test down -v -t 1 > /dev/null 2>&1
}

run_task() {
  local c=$1 r=$2 port=$3 p=mhbt-$1-$2
  CLIENT_MODE=
  . "conditions/$c.env"
  files=(-f docker-compose.yml)
  [ ! -f "conditions/$c.yml" ] || files+=(-f "conditions/$c.yml")
  HOST_PORT=$port
  export CLIENT_MODE HOST_PORT
  docker compose -p "$p" "${files[@]}" --profile test down -v -t 1 > /dev/null 2>&1
  run_once "$c" "$p" "$port" "$results/$c/run$r" "$([ "$c$r" = baseline1 ] && echo 1 || echo 0)"
}

rc=0
for j in "${jobs[@]}"; do
  t0=$(date +%s)
  read -r -a conds < <(awk -F'\t' -v n="$j" '$1 == n { print $2 }' jobs.tsv)
  echo "== $j: ${conds[*]}" >&2
  port=33100
  for c in "${conds[@]}"; do
    mkdir -p "$results/$c"
    echo "$j" > "$results/$c/job.txt"
    for r in $(seq "$RUNS"); do
      port=$((port + 1))
      while [ "$(jobs -rp | wc -l)" -ge "$PARALLEL" ]; do wait -n; done
      run_task "$c" "$r" "$port" &
    done
  done
  wait
  printf '%s\t%s\n' "$j" "$(( $(date +%s) - t0 ))" >> "$results/timing.tsv"
done
printf '%s\t%s\n' all "$(( $(date +%s) - start_all ))" >> "$results/timing.tsv"

python3 scripts/summary.py "$results" "${jobs[@]}" > "$results/summary.md" || rc=1
cat "$results/summary.md"
exit $rc
