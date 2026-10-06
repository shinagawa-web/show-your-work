#!/usr/bin/env bash
# Runs as root inside the test VM after setup.sh. For each condition and run,
# records docker events, the kernel log (OOM killer records), docker inspect
# and dockerd's journal under $results/<cond>/<run>/.
#   conditions.sh [COND...]   (default: C1 ... C11)
# Runs per condition: RUNS_<COND> (default from RUNS_DEFAULT below).
set -uo pipefail
results=${RESULTS:-/root/results}
IMG=exit137/subject:local
mkdir -p "$results"
# More than one run only where the outcome may vary between runs: the order
# of the oom and die events (C1, C2, C6) and which process the global OOM
# killer picks and whether OOMKilled/oom show up (C2).
declare -A RUNS_DEFAULT=([C1]=3 [C2]=10 [C6]=3)

now() { date +%s.%N; }
el() { awk -v a="$(now)" -v b="$1" 'BEGIN{printf "%.1f", a-b}'; }
kcursor() { journalctl -k -n 0 --show-cursor -q | sed -n 's/^-- cursor: //p'; }
dcursor() { journalctl -u docker -n 0 --show-cursor -q | sed -n 's/^-- cursor: //p'; }
ccursor() { journalctl -u containerd -n 0 --show-cursor -q | sed -n 's/^-- cursor: //p'; }
running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
wait_log() { # name pattern timeout_s
  local i
  for i in $(seq $(( $3 * 20 ))); do
    docker logs "$1" 2>&1 | grep -q "$2" && return 0
    sleep 0.05
  done
  echo "timeout waiting for '$2' in $1" | tee -a "$dir/notes.txt"
  return 1
}

begin() {
  cond=$1; run=$2; name="$(echo "$1" | tr A-Z a-z)-$2"
  dir=$results/$cond/$run
  rm -rf "$dir"; mkdir -p "$dir"
  docker rm -f "$name" "$name-filler" >/dev/null 2>&1
  t_start=$(now); kc=$(kcursor); dc=$(dcursor); cc=$(ccursor)
  echo "== $cond run $run ($name)"
}
# Right after start: State.Pid (PID 1 seen from the host), HostConfig.Init,
# and PID 1's children.
at_start() {
  docker inspect "$name" > "$dir/inspect-start.json"
  pid1=$(docker inspect -f '{{.State.Pid}}' "$name")
  child=$(cat "/proc/$pid1/task/$pid1/children" 2>/dev/null | tr -d ' ')
  if [ "${PROBE:-0}" = 1 ]; then
    local cg=/sys/fs/cgroup/system.slice/docker-$(docker inspect -f '{{.Id}}' "$name").scope
    ( echo -1000 > /proc/self/oom_score_adj; exec "$(dirname "$0")/cgprobe" "$cg" ) > "$dir/cgprobe.txt" 2>&1 &
    probe_pid=$!
  fi
  if [ "${TRACE:-0}" = 1 ]; then
    shim_pid=$(pgrep -f "containerd-shim-runc-v2 -namespace moby -id $(docker inspect -f '{{.Id}}' "$name")")
    echo "shim pid=$shim_pid stat at start: $(cut -d' ' -f10-13 /proc/$shim_pid/stat) (minflt cminflt majflt cmajflt)" > "$dir/shim.txt"
  fi
  {
    echo "State.Pid=$pid1 HostConfig.Init=$(docker inspect -f '{{.HostConfig.Init}}' "$name") children=$child"
    ps -o pid,ppid,comm,args --ppid "$pid1" -p "$pid1"
  } | tee "$dir/start.txt"
}
finish() {
  sleep 0.5
  local t_end; t_end=$(now)
  # filter by ID: the name filter also matches "$name-filler"
  docker events --since "$t_start" --until "$t_end" --filter "container=$(docker inspect -f '{{.Id}}' "$name")" --format '{{json .}}' > "$dir/events.jsonl"
  if docker inspect "$name-filler" >/dev/null 2>&1; then
    docker events --since "$t_start" --until "$t_end" --filter "container=$(docker inspect -f '{{.Id}}' "$name-filler")" --format '{{json .}}' > "$dir/events-filler.jsonl"
    docker inspect "$name-filler" > "$dir/inspect-filler.json"
    docker logs "$name-filler" > "$dir/container-filler.log" 2>&1
  fi
  journalctl -k --after-cursor="$kc" -o short-iso-precise -q > "$dir/kernel.txt"
  journalctl -u docker --after-cursor="$dc" -o short-iso-precise -q > "$dir/dockerd.txt"
  journalctl -u containerd --after-cursor="$cc" -o short-iso-precise -q > "$dir/containerd.txt"
  docker inspect "$name" > "$dir/inspect-end.json"
  if [ -n "${shim_pid:-}" ]; then
    echo "shim pid=$shim_pid stat at end: $(cut -d' ' -f10-13 /proc/$shim_pid/stat 2>&1) (minflt cminflt majflt cmajflt)" >> "$dir/shim.txt"
    shim_pid=
  fi
  docker logs "$name" > "$dir/container.log" 2>&1
  python3 "$(dirname "$0")/summarize.py" "$dir" | tee "$dir/summary.txt"
  docker rm -f "$name" "$name-filler" >/dev/null 2>&1
  if [ -n "${probe_pid:-}" ]; then
    sleep 0.2; kill "$probe_pid" 2>/dev/null; wait "$probe_pid" 2>/dev/null; probe_pid=
  fi
}

# C1: container memory limit, subject (PID 1) allocates past it
C1() {
  docker run -d --name "$name" --memory 128m "$IMG" alloc 256 16 0 300 >/dev/null
  at_start
  timeout 30 docker wait "$name" > "$dir/wait.txt"
  finish
}

# C2: host-wide OOM. The subject holds more than the unlimited filler holds
# when memory runs out; the subject's own limit is out of its reach. The
# filler stops at 80% of MemAvailable, so it does not run out of memory again
# after the subject is gone.
C2() {
  local avail alloc
  sync; echo 3 > /proc/sys/vm/drop_caches
  avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
  alloc=$(( avail * 6 / 10 ))
  echo "MemAvailable=${avail}MiB subject_alloc=${alloc}MiB subject_limit=$((alloc + 128))MiB filler_max=$(( avail * 8 / 10 ))MiB" | tee "$dir/sizing.txt"
  docker run -d --name "$name" --memory "$((alloc + 128))m" "$IMG" alloc "$alloc" 16 0 0 >/dev/null
  wait_log "$name" holding 30
  at_start
  docker run -d --name "$name-filler" --entrypoint /filler "$IMG" alloc $(( avail * 8 / 10 )) 4 10 0 >/dev/null
  local t0; t0=$(now)
  while running "$name" && running "$name-filler"; do
    [ "$(el "$t0" | cut -d. -f1)" -ge 90 ] && { echo "timeout: both still running" | tee -a "$dir/notes.txt"; break; }
    sleep 0.05
  done
  echo "seconds until one exited: $(el "$t0")" | tee -a "$dir/sizing.txt"
  echo "subject running=$(docker inspect -f '{{.State.Running}}' "$name") filler running=$(docker inspect -f '{{.State.Running}}' "$name-filler")" | tee -a "$dir/sizing.txt"
  docker kill "$name-filler" >/dev/null 2>&1
  finish
}

# C3: docker stop -t 2 (no SIGTERM handler in the subject)
C3() {
  docker run -d --name "$name" "$IMG" alloc 16 16 0 0 >/dev/null
  wait_log "$name" holding 10
  at_start
  local t0; t0=$(now)
  docker stop -t 2 "$name" > /dev/null
  echo "docker stop -t 2 seconds: $(el "$t0")" | tee "$dir/action.txt"
  finish
}

# C4: docker kill
C4() {
  docker run -d --name "$name" "$IMG" alloc 16 16 0 0 >/dev/null
  wait_log "$name" holding 10
  at_start
  docker kill "$name" > "$dir/action.txt" 2>&1
  timeout 10 docker wait "$name" > "$dir/wait.txt"
  finish
}

# C5: kill -9 to PID 1 from the host
C5() {
  docker run -d --name "$name" "$IMG" alloc 16 16 0 0 >/dev/null
  wait_log "$name" holding 10
  at_start
  echo "\$ kill -9 $pid1" > "$dir/action.txt"
  kill -9 "$pid1" >> "$dir/action.txt" 2>&1
  timeout 10 docker wait "$name" > "$dir/wait.txt"
  finish
}

# C6: --init, the child (subject) allocates past the limit
C6() {
  docker run -d --init --name "$name" --memory 128m "$IMG" alloc 256 16 0 300 >/dev/null
  wait_log "$name" started 10
  at_start
  timeout 30 docker wait "$name" > "$dir/wait.txt"
  finish
}

# C7: --init, kill -9 to tini's child from the host
C7() {
  docker run -d --init --name "$name" "$IMG" alloc 16 16 0 0 >/dev/null
  wait_log "$name" holding 10
  at_start
  echo "\$ kill -9 $child" > "$dir/action.txt"
  kill -9 "$child" >> "$dir/action.txt" 2>&1
  timeout 10 docker wait "$name" > "$dir/wait.txt"
  finish
}

# C8: the subject returns 137 by itself
C8() {
  docker run -d --name "$name" "$IMG" exit 137 300 >/dev/null
  at_start
  timeout 10 docker wait "$name" > "$dir/wait.txt"
  finish
}

# C9: kill -9 to PID 1 from inside the container
C9() {
  docker run -d --name "$name" "$IMG" alloc 16 16 0 0 >/dev/null
  wait_log "$name" holding 10
  at_start
  {
    echo "\$ docker exec $name kill -9 1"
    docker exec "$name" kill -9 1; echo "[exit $?]"
    sleep 3
    echo "after 3s: Running=$(docker inspect -f '{{.State.Running}}' "$name") State.Pid=$(docker inspect -f '{{.State.Pid}}' "$name")"
  } 2>&1 | tee "$dir/action.txt"
  finish
}

# C10: C1 with --restart on-failure; allocate only on the first start
C10() {
  docker run -d --restart on-failure --name "$name" --memory 128m "$IMG" alloc 256 16 0 300 /marker >/dev/null
  at_start
  wait_log "$name" exists 30
  sleep 0.3
  echo "while running after restart: Running=$(docker inspect -f '{{.State.Running}}' "$name")" | tee "$dir/action.txt"
  finish
}

# C11: C4 with --restart on-failure
C11() {
  docker run -d --restart on-failure --name "$name" "$IMG" alloc 16 16 0 0 >/dev/null
  wait_log "$name" holding 10
  at_start
  docker kill "$name" > /dev/null 2>&1
  sleep 3
  echo "3s after docker kill: Running=$(docker inspect -f '{{.State.Running}}' "$name") RestartCount=$(docker inspect -f '{{.RestartCount}}' "$name")" | tee "$dir/action.txt"
  finish
}

# When sourced (by investigate.sh), only define the functions.
[[ "${BASH_SOURCE[0]}" != "$0" ]] && return 0

conds=${*:-C1 C2 C3 C4 C5 C6 C7 C8 C9 C10 C11}
for c in $conds; do
  tc=$(now)
  v=RUNS_$c
  n=${!v:-${RUNS_DEFAULT[$c]:-1}}
  for r in $(seq "$n"); do
    begin "$c" "$r"
    {
      echo "containers=$(docker ps -aq | wc -l)"
      echo "docker_scopes=$(ls -d /sys/fs/cgroup/system.slice/docker-*.scope 2>/dev/null | wc -l)"
      grep MemAvailable /proc/meminfo
    } > "$dir/pre.txt"
    "$c"
    echo "$c run $r seconds=$(el "$t_start")" | tee -a "$results/timing.txt"
  done
  echo "$c total seconds=$(el "$tc") runs=$n" | tee -a "$results/timing.txt"
done
for f in buffer_size_kb tracing_on kprobe_events set_event; do
  echo "== $f"; cat /sys/kernel/tracing/$f
done > "$results/ftrace-state.txt" 2>&1
