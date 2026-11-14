#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
NODE_IMAGE=node:26.13.1-trixie
RUNS=${RUNS:-3}
PARALLEL=${PARALLEL:-4}
mapfile -t all < <(awk -F'\t' 'NR > 1 { print $1 }' conditions.tsv)
if [ $# -gt 0 ]; then conds=("$@"); else conds=("${all[@]}"); fi
for c in "${conds[@]}"; do
  case " ${all[*]} " in *" $c "*) ;; *) echo "unknown condition: $c (one of: ${all[*]})" >&2; exit 2 ;; esac
done
results=$here/results
rm -rf "$results"; mkdir -p "$results"
now() { date +%s.%N; }
since() { awk -v a="$(now)" -v b="$1" 'BEGIN { printf "%.1f", a - b }'; }
t_start=$(now)

docker pull -q "$NODE_IMAGE" > "$results/pull.log" 2>&1 || { cat "$results/pull.log"; exit 1; }
targets=$(awk -F'\t' 'NR > 1 { print $2 }' conditions.tsv | sort -u)
for t in $targets; do
  docker build --build-arg NODE_IMAGE="$NODE_IMAGE" --target "$t" -t "dicr:$t" app >> "$results/build.log" 2>&1 || { tail -30 "$results/build.log"; exit 1; }
done
t_built=$(now)

{
  echo "\$ uname -srm"; uname -srm
  echo "\$ docker version --format '{{.Server.Version}}'"; docker version --format '{{.Server.Version}}'
  echo "\$ docker info --format '{{.InitBinary}}'"; docker info --format '{{.InitBinary}}'
  init_bin=$(command -v docker-init || echo /usr/libexec/docker/docker-init)
  echo "\$ $init_bin --version"; "$init_bin" --version
  echo "\$ docker image inspect $NODE_IMAGE --format '{{json .RepoDigests}}'"; docker image inspect "$NODE_IMAGE" --format '{{json .RepoDigests}}'
  echo "\$ docker run --rm --init dicr:exec-form /sbin/docker-init --version"; docker run --rm --init dicr:exec-form /sbin/docker-init --version
  echo "\$ docker run --rm dicr:exec-form node --version"; docker run --rm dicr:exec-form node --version
  echo "\$ docker run --rm dicr:exec-form node -p \"require('express/package.json').version\""; docker run --rm dicr:exec-form node -p "require('express/package.json').version"
  echo "\$ docker run --rm dicr:exec-form sh -c 'cat /etc/debian_version; readlink -f /bin/sh; dpkg-query -W dash'"; docker run --rm dicr:exec-form sh -c 'cat /etc/debian_version; readlink -f /bin/sh; dpkg-query -W dash'
} > "$results/environment.txt" 2>&1

wait_log() {
  local name=$1 pattern=$2 limit=$3 i
  for i in $(seq $((limit * 20))); do
    docker logs "$name" 2>&1 | grep -q -- "$pattern" && return 0
    sleep 0.05
  done
  return 1
}

invalid() { echo "$2" >> "$1/invalid.txt"; }

run_one() {
  local c=$1 r=$2 port=$3 out=$results/$1/run$2 name=dicr-$1-$2
  local target init handler seconds args cpid spid t0 t1 id
  IFS=$'\t' read -r _ target init handler seconds _ < <(awk -F'\t' -v n="$c" '$1 == n' conditions.tsv)
  mkdir -p "$out"
  docker rm -f "$name" > /dev/null 2>&1
  args=(-d --name "$name" -p "127.0.0.1:$port:3000" -e "SIGTERM_HANDLER=$handler")
  [ "$init" = 1 ] && args+=(--init)
  echo "docker run ${args[*]} dicr:$target" > "$out/docker-run.txt"
  docker run "${args[@]}" "dicr:$target" >> "$out/docker-run.txt" 2>&1 || { invalid "$out" "docker run failed"; return; }
  wait_log "$name" "listening on 3000" 30 || { invalid "$out" "the server did not log listening within 30 s"; docker logs "$name" > "$out/app.log" 2>&1; docker rm -f "$name" > /dev/null 2>&1; return; }
  docker top "$name" > "$out/docker-top.txt" 2>&1
  docker exec "$name" ps -eo pid,ppid,args > "$out/ps-in-container.txt" 2>&1
  docker exec -i "$name" sh -s < scripts/proc-signals.sh > "$out/proc-signals-before.txt" 2>&1
  (
    curl -sS -o "$out/curl-body.txt" -w 'http_code=%{http_code} time_total=%{time_total}\n' "http://127.0.0.1:$port/slow?seconds=$seconds" > "$out/curl-stdout.txt" 2> "$out/curl-stderr.txt"
    echo $? > "$out/curl-exit.txt"
  ) &
  cpid=$!
  wait_log "$name" "request accepted" 10 || invalid "$out" "the server did not log the request within 10 s"
  [ "$(docker inspect -f '{{.State.Running}}' "$name")" = true ] || invalid "$out" "the container was not running before docker stop"
  ( sleep 1; docker exec -i "$name" sh -s < scripts/proc-signals.sh > "$out/proc-signals-after-1s.txt" 2>&1 ) &
  spid=$!
  t0=$(now)
  docker stop "$name" > "$out/docker-stop.txt" 2>&1
  t1=$(now)
  wait "$cpid" "$spid"
  awk -v a="$t1" -v b="$t0" 'BEGIN { printf "%.2f\n", a - b }' > "$out/stop-seconds.txt"
  docker inspect "$name" > "$out/inspect.json" 2>&1
  docker inspect -f '{{.State.ExitCode}}' "$name" > "$out/exit-code.txt" 2>&1
  docker logs -t "$name" > "$out/app.log" 2>&1
  id=$(docker inspect -f '{{.Id}}' "$name")
  sleep 1
  sudo -n journalctl -u docker --since "@${t0%.*}" -o short-iso-precise --no-pager 2>&1 | grep -F -- "$id" > "$out/dockerd.log"
  docker rm -f "$name" > /dev/null 2>&1
}

trace=/sys/kernel/tracing/instances/dicr
sudo -n sh -c "rmdir $trace 2> /dev/null; mkdir $trace && echo 'sig == 9 || sig == 15' > $trace/events/signal/signal_generate/filter && echo 1 > $trace/events/signal/signal_generate/enable" > "$results/trace-setup.txt" 2>&1 || { cat "$results/trace-setup.txt"; exit 1; }

t_run=$(now)
port=13000
for c in "${conds[@]}"; do
  for r in $(seq "$RUNS"); do
    port=$((port + 1))
    while [ "$(jobs -rp | wc -l)" -ge "$PARALLEL" ]; do wait -n; done
    run_one "$c" "$r" "$port" &
  done
done
wait
sudo -n cat "$trace/trace" > "$results/signal-trace.txt"
sudo -n sh -c "echo 0 > $trace/events/signal/signal_generate/enable; rmdir $trace"
for d in "$results"/*/run*; do
  pids=$(awk 'NR > 1 { print $2 }' "$d/docker-top.txt" | paste -sd '|')
  [ -n "$pids" ] && grep -E " pid=($pids) " "$results/signal-trace.txt" > "$d/signals.txt"
done
{
  echo "build: $(awk -v a="$t_built" -v b="$t_start" 'BEGIN { printf "%.1f", a - b }') s"
  echo "conditions: $(since "$t_run") s"
  echo "total: $(since "$t_start") s"
} > "$results/timing.txt"

python3 scripts/summary.py "$results" "${conds[@]}" > "$results/summary.md"
rc=$?
cat "$results/summary.md" "$results/timing.txt"
exit $rc
