#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
export TZ=UTC
t_script_start=$(date +%s.%N)
IMAGE=mongodb/mongodb-atlas-local@sha256:ca394bdaedef5d802a07f8e00e7734f181ee9b6cc1aacbf09a50fb776657edbf
AUTH_USER=e2e
AUTH_PASS=e2e-password
OOM_MEMORY=1g
OOM_HEALTH_WATCH_S=120
results=$here/results
all=(wait nowait mongoose auth health-start-interval mongot-oom sample-data)
declare -A runs=([wait]=5 [nowait]=1 [mongoose]=5 [auth]=1 [health-start-interval]=1 [mongot-oom]=1 [sample-data]=1)

usage() {
  echo "usage: ./run-all.sh [job[:runs] ...]            local runs, jobs: ${all[*]}" >&2
  echo "       ./run-all.sh <job> seed|test|collect     with SERVICE_CONTAINER and RUN set (GitHub Actions services)" >&2
  exit 2
}

is_job() { case " ${all[*]} " in *" $1 "*) return 0 ;; esac; return 1; }

job_uri() {
  if [ "$1" = auth ]; then
    echo "mongodb://$AUTH_USER:$AUTH_PASS@localhost:27017/?directConnection=true"
  else
    echo "mongodb://localhost:27017/?directConnection=true"
  fi
}

job_docker_args() {
  case $1 in
    auth) echo "-e MONGODB_INITDB_ROOT_USERNAME=$AUTH_USER -e MONGODB_INITDB_ROOT_PASSWORD=$AUTH_PASS" ;;
    health-start-interval) echo "--health-start-interval 1s" ;;
    mongot-oom) echo "--memory $OOM_MEMORY --memory-swap $OOM_MEMORY" ;;
    sample-data) echo "-e MONGODB_LOAD_SAMPLE_DATA=true" ;;
  esac
}

now() { date +%s.%N; }

wait_log() {
  local ctr=$1 pattern=$2 limit=$3 i
  for i in $(seq $((limit * 5))); do
    docker logs "$ctr" 2>&1 | grep -F -- "$pattern" > /dev/null && return 0
    sleep 0.2
  done
  return 1
}

npm_ci() {
  local out=$1
  echo "t_npm_start=$(now)" >> "$out/steps.env"
  npm ci --no-audit --no-fund > "$out/npm-ci.log" 2>&1 || { cat "$out/npm-ci.log" >&2; exit 1; }
  echo "t_npm_end=$(now)" >> "$out/steps.env"
}

write_environment() {
  {
    echo "\$ uname -srm"; uname -srm
    echo "\$ nproc"; nproc
    echo "\$ free -m"; free -m
    echo "\$ docker version --format '{{.Server.Version}}'"; docker version --format '{{.Server.Version}}'
    echo "\$ docker info --format '{{.CgroupVersion}} {{.CgroupDriver}}'"; docker info --format '{{.CgroupVersion}} {{.CgroupDriver}}'
    echo "\$ docker image inspect IMAGE --format '{{json .RepoDigests}} {{.Os}}/{{.Architecture}} {{.Created}}'"
    docker image inspect "$IMAGE" --format '{{json .RepoDigests}} {{.Os}}/{{.Architecture}} {{.Created}}'
    echo "\$ docker image inspect IMAGE --format '{{json .Config.Healthcheck}}'"; docker image inspect "$IMAGE" --format '{{json .Config.Healthcheck}}'
    echo "\$ docker image inspect IMAGE --format '{{json .Config.Volumes}} {{json .Config.Cmd}}'"; docker image inspect "$IMAGE" --format '{{json .Config.Volumes}} {{json .Config.Cmd}}'
    echo "\$ node --version"; node --version
    echo "\$ npm ls mongodb mongoose"; npm ls mongodb mongoose
  } > "$results/environment.txt" 2>&1
}

cgroup_dir() {
  local id d
  id=$(docker inspect -f '{{.Id}}' "$1")
  for d in "/sys/fs/cgroup/system.slice/docker-$id.scope" "/sys/fs/cgroup/docker/$id"; do
    [ -d "$d" ] && { echo "$d"; return; }
  done
  find /sys/fs/cgroup -maxdepth 3 -type d -name "*$id*" 2> /dev/null | head -1
}

mongot_pid() { docker top "$1" -eo pid,args 2> /dev/null | awk '/MongotCli/ {print $1; exit}'; }

oom_mongot() {
  local out=$1 ctr=$2 cg limit_kb total_kb mongot_kb hog_kb mongod hog="" t_end n before p
  cg=$(cgroup_dir "$ctr")
  echo "cgroup=$cg" >> "$out/oom.env"
  limit_kb=$(($(docker inspect -f '{{.HostConfig.Memory}}' "$ctr") / 1024))
  docker top "$ctr" -eo pid,rss,args > "$out/top-before.txt"
  total_kb=$(awk 'NR > 1 {s += $2} END {print s}' "$out/top-before.txt")
  mongot_kb=$(awk 'NR > 1 && /MongotCli/ {print $2; exit}' "$out/top-before.txt")
  hog_kb=$((limit_kb - total_kb + ${mongot_kb:-0} / 2))
  echo "limit_kb=$limit_kb total_rss_kb=$total_kb mongot_rss_kb=$mongot_kb hog_kb=$hog_kb" >> "$out/oom.env"
  cat "$cg/memory.events" > "$out/memory.events.before" 2>&1
  before=$(awk '$1 == "oom_kill" {print $2}' "$out/memory.events.before")
  mongod=$(docker top "$ctr" -eo pid,args | awk '$2 == "mongod" {print $1; exit}')
  echo -1000 | sudo tee "/proc/$mongod/oom_score_adj" > /dev/null
  echo "mongod_pid=$mongod mongod_oom_score_adj=$(cat "/proc/$mongod/oom_score_adj")" >> "$out/oom.env"
  docker exec -u root -d "$ctr" sh -c "while [ ! -f /tmp/hog.go ]; do sleep 0.1; done; exec sh -c 'head -c ${hog_kb}k /dev/zero | tail > /dev/null'"
  for _ in $(seq 50); do
    hog=$(docker top "$ctr" -eo pid,args | awk '/hog.go/ {print $1; exit}')
    [ -n "$hog" ] && break
    sleep 0.1
  done
  echo -1000 | sudo tee "/proc/$hog/oom_score_adj" > /dev/null
  echo "hog_pid=$hog hog_oom_score_adj=$(cat "/proc/$hog/oom_score_adj")" >> "$out/oom.env"
  echo "t_hog_go=$(now)" >> "$out/oom.env"
  docker exec -u root "$ctr" touch /tmp/hog.go
  t_end=$(($(date +%s) + 30))
  printf 't\tmemory_current\toom_kill\tmongot_pid\n' > "$out/oom-poll.tsv"
  while [ "$(date +%s)" -lt "$t_end" ]; do
    n=$(awk '$1 == "oom_kill" {print $2}' "$cg/memory.events")
    p=$(mongot_pid "$ctr")
    printf '%s\t%s\t%s\t%s\n' "$(now)" "$(cat "$cg/memory.current")" "$n" "$p" >> "$out/oom-poll.tsv"
    if [ "${n:-0}" -gt "${before:-0}" ] && [ -z "$p" ]; then break; fi
    sleep 0.2
  done
  echo "t_oom_seen=$(now)" >> "$out/oom.env"
  cat "$cg/memory.events" > "$out/memory.events.after" 2>&1
  docker top "$ctr" -eo pid,rss,args > "$out/top-after-oom.txt"
  for p in $(docker top "$ctr" -eo pid | tail -n +2); do
    printf '%s\t%s\t%s\n' "$p" "$(cat "/proc/$p/oom_score_adj" 2> /dev/null)" "$(tr '\0' ' ' < "/proc/$p/cmdline" 2> /dev/null | cut -c1-80)"
  done > "$out/oom-score-adj.tsv"
}

watch_health() {
  local out=$1 ctr=$2 limit=$3 t_end s
  t_end=$(($(date +%s) + limit))
  : > "$out/health-poll.jsonl"
  while [ "$(date +%s)" -lt "$t_end" ]; do
    s=$(docker inspect -f '{{json .State.Health}}' "$ctr")
    printf '{"t":%s,"mongot_pid":"%s","health":%s}\n' "$(now)" "$(mongot_pid "$ctr")" "$s" >> "$out/health-poll.jsonl"
    case $s in *'"Status":"unhealthy"'*) break ;; esac
    sleep 2
  done
}

phase_seed() {
  local out=$1 probe rc
  mkfifo "$out/probe.fifo"
  exec 3<> "$out/probe.fifo"
  echo "t_probe_start=$(now)" >> "$out/docker-run.env"
  node scripts/list-indexes.mjs < "$out/probe.fifo" > "$out/listindexes.jsonl" 2> "$out/listindexes.err" &
  probe=$!
  echo "t_seed_start=$(now)" >> "$out/docker-run.env"
  MONGOOSE_DEBUG=${MONGOOSE_DEBUG:-0} node scripts/mongoose-step.mjs seed > "$out/seed.jsonl" 2> "$out/seed.err"
  rc=$?
  echo "seed_rc=$rc t_seed_end=$(now)" >> "$out/docker-run.env"
  echo go >&3
  wait "$probe"
  rc=$?
  echo "probe_rc=$rc t_probe_end=$(now)" >> "$out/docker-run.env"
  exec 3>&-
  rm -f "$out/probe.fifo"
}

phase_test() {
  local job=$1 out=$2 ctr=$3 rc mode=wait
  if [ "$job" = mongoose ]; then
    echo "t_test_start=$(now)" >> "$out/docker-run.env"
    MONGOOSE_DEBUG=${MONGOOSE_DEBUG:-0} node scripts/mongoose-step.mjs test > "$out/test.jsonl" 2> "$out/test.err"
    rc=$?
    echo "test_rc=$rc t_test_end=$(now)" >> "$out/docker-run.env"
    return
  fi
  [ "$job" = nowait ] && mode=nowait
  [ "$job" = mongot-oom ] && oom_mongot "$out" "$ctr"
  echo "t_test_start=$(now)" >> "$out/docker-run.env"
  if [ "$job" = mongot-oom ]; then
    QUERYABLE_DEADLINE_S=30 MONGODB_URI=$(job_uri "$job") node scripts/test.mjs "$mode" > "$out/test.jsonl" 2> "$out/test.err"
  else
    MONGODB_URI=$(job_uri "$job") node scripts/test.mjs "$mode" > "$out/test.jsonl" 2> "$out/test.err"
  fi
  rc=$?
  echo "test_rc=$rc t_test_end=$(now)" >> "$out/docker-run.env"
  if [ "$job" = mongot-oom ]; then
    watch_health "$out" "$ctr" "$OOM_HEALTH_WATCH_S"
    sudo dmesg --time-format iso > "$out/dmesg.txt" 2>&1
    cat "$(cgroup_dir "$ctr")/memory.events" > "$out/memory.events.end" 2>&1
    docker top "$ctr" -eo pid,rss,args > "$out/top-end.txt"
  fi
}

phase_collect() {
  local job=$1 out=$2 ctr=$3 limit=180 id created
  [ "$job" = mongot-oom ] && limit=10
  wait_log "$ctr" "waiting for user to cancel context" "$limit" || echo "the image runner did not log 'waiting for user to cancel context' within $limit s" >> "$out/invalid.txt"
  sleep 1
  docker logs -t "$ctr" > "$out/container.log" 2>&1
  docker inspect "$ctr" > "$out/inspect.json" 2>&1
  docker top "$ctr" -eo pid,rss,args > "$out/top-collect.txt" 2>&1
  if [ ! -s "$out/events.jsonl" ]; then
    id=$(docker inspect -f '{{.Id}}' "$ctr")
    created=$(docker inspect -f '{{.Created}}' "$ctr")
    docker events --since "$created" --until "$(date +%s.%N)" --filter "container=$id" --format '{{json .}}' > "$out/events.jsonl" 2> "$out/events.err"
  fi
}

summarize() {
  local present=() j
  for j in "${all[@]}"; do [ -d "$results/$j" ] && present+=("$j"); done
  python3 scripts/summary.py "$results" "${present[@]}" > "$results/summary.md"
}

if [ -n "${SERVICE_CONTAINER:-}" ]; then
  { [ $# -eq 2 ] && is_job "$1"; } || usage
  job=$1 phase=$2 ctr=$SERVICE_CONTAINER
  out=$results/$job/run${RUN:?RUN is not set}
  mkdir -p "$out"
  echo "t_${phase}_step_start=$t_script_start" >> "$out/steps.env"
  case $phase in
    seed)
      [ "$job" = mongoose ] || usage
      npm_ci "$out"
      phase_seed "$out"
      ;;
    test)
      [ -d node_modules ] || npm_ci "$out"
      phase_test "$job" "$out" "$ctr"
      ;;
    collect)
      phase_collect "$job" "$out" "$ctr"
      write_environment
      summarize
      rc=$?
      cat "$results/summary.md"
      exit $rc
      ;;
    *) usage ;;
  esac
  exit 0
fi

if [ $# -gt 0 ]; then args=("$@"); else args=("${all[@]}"); fi
jobs=()
for a in "${args[@]}"; do
  j=${a%%:*}
  is_job "$j" || usage
  [ "$a" = "$j" ] || runs[$j]=${a#*:}
  jobs+=("$j")
done
mkdir -p "$results"
for j in "${jobs[@]}"; do rm -rf "${results:?}/$j"; done
start_all=$(date +%s)
npm ci --no-audit --no-fund > "$results/npm-ci.log" 2>&1 || { cat "$results/npm-ci.log" >&2; exit 1; }
docker pull -q "$IMAGE" > "$results/pull.log" 2>&1 || { cat "$results/pull.log" >&2; exit 1; }
write_environment
[ -f "$results/timing.tsv" ] || printf 'job\tseconds\n' > "$results/timing.tsv"

run_once() {
  local job=$1 out=$2 name=$3 ev rc
  mkdir -p "$out"
  docker ps -aq --filter name=alhf- | xargs -r docker rm -f -v > /dev/null 2>&1
  docker events --filter "container=$name" --format '{{json .}}' > "$out/events.jsonl" 2> "$out/events.err" &
  ev=$!
  sleep 1
  echo "t_run_start=$(now)" > "$out/docker-run.env"
  docker run -d --name "$name" -p 27017:27017 \
    -e RUNNER_LOG_FILE=/dev/stdout -e MONGOT_LOG_FILE=/dev/stdout \
    $(job_docker_args "$job") "$IMAGE" > "$out/docker-run.log" 2>&1
  rc=$?
  echo "rc=$rc t_run_returned=$(now)" >> "$out/docker-run.env"
  if [ "$rc" != 0 ]; then
    echo "docker run failed: $(tail -n 1 "$out/docker-run.log")" >> "$out/invalid.txt"
    kill "$ev" 2> /dev/null; wait "$ev" 2> /dev/null
    return
  fi
  python3 scripts/wait_healthy.py "$name" mongodb > "$out/runner-wait.tsv" 2> "$out/runner-wait.err"
  echo "wait_rc=$?" >> "$out/docker-run.env"
  [ "$job" = mongoose ] && phase_seed "$out"
  phase_test "$job" "$out" "$name"
  phase_collect "$job" "$out" "$name"
  kill "$ev" 2> /dev/null; wait "$ev" 2> /dev/null
  docker rm -f -v "$name" > /dev/null 2>&1
}

for j in "${jobs[@]}"; do
  t0=$(date +%s)
  for r in $(seq "${runs[$j]}"); do
    echo "== $j run$r" >&2
    run_once "$j" "$results/$j/run$r" "alhf-$j-$r"
  done
  if [ "$j" = mongoose ]; then
    for r in $(seq "${runs[$j]}"); do
      echo "== $j debug-run$r" >&2
      MONGOOSE_DEBUG=1 run_once mongoose "$results/$j/debug-run$r" "alhf-$j-debug-$r"
    done
  fi
  printf '%s\t%s\n' "$j" "$(( $(date +%s) - t0 ))" >> "$results/timing.tsv"
done
printf '%s\t%s\n' all "$(( $(date +%s) - start_all ))" >> "$results/timing.tsv"

summarize
rc=$?
cat "$results/summary.md"
exit $rc
