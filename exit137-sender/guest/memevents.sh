#!/usr/bin/env bash
set -uo pipefail
. "$(dirname "$0")/conditions.sh"
probe=$(dirname "$0")/cgprobe
ss=/sys/fs/cgroup/system.slice

mev() { if [ -r "$1/memory.events" ]; then tr '\n' ' ' < "$1/memory.events" | sed 's/ $//'; else echo absent; fi; }
oom_kill() { awk '$1 == "oom_kill" {print $2}' "$1/memory.events"; }
present() { [ -e "$1" ] && echo yes || echo no; }
out() { echo "$*" | tee -a "$dir/memevents.txt"; }

record_env() {
  out "boot_id=$(cat /proc/sys/kernel/random/boot_id) uptime_seconds=$(cut -d' ' -f1 /proc/uptime) CgroupDriver=$(docker info -f '{{.CgroupDriver}}') /sys/fs/cgroup/docker present=$(present /sys/fs/cgroup/docker)"
  out "cgroup2 mount: $(awk '$3 == "cgroup2"' /proc/mounts)"
  ss_before=$(oom_kill $ss)
  out "system.slice memory.events before docker run: $(mev $ss)"
}

run_subject() {
  cid=$(docker run -d --name "$name" "$@") || return 1
  cg=$ss/docker-$cid.scope
  out "container cgroup memory.events right after docker run: $(mev "$cg")"
  out "container cgroup inode right after docker run: $(stat -c %i "$cg")"
  ( echo -1000 > /proc/self/oom_score_adj; exec "$probe" "$cg" ) > "$dir/cgprobe.txt" 2>&1 &
  probe_pid=$!
}

after_exit() {
  out "right after exit: cgroup dir present=$(present "$cg") memory.events=$(mev "$cg")"
  sleep 2
  out "2s after exit: cgroup dir present=$(present "$cg") memory.events=$(mev "$cg")"
}

system_slice_after() {
  local a; a=$(oom_kill $ss)
  out "system.slice memory.events at end: $(mev $ss)"
  out "system.slice oom_kill delta=$((a - ss_before))"
}

probe_last() {
  out "last memory.events line in cgprobe.txt: $(grep -o 'memory.events: [^|]*' "$dir/cgprobe.txt" | tail -1 | sed 's/^memory.events: //; s/ *$//')"
}

M1() {
  record_env
  run_subject --memory 128m "$IMG" alloc 256 16 0 300 /marker
  wait_started
  at_start
  timeout 30 docker wait "$name" > "$dir/wait.txt"
  after_exit
  system_slice_after
  finish
  probe_last
}

M2() {
  record_env
  run_subject --restart on-failure --memory 128m "$IMG" alloc 256 16 0 300 /marker
  wait_started
  at_start
  wait_log "$name" exists 30
  sleep 0.3
  out "after restart: Running=$(docker inspect -f '{{.State.Running}}' "$name") RestartCount=$(docker inspect -f '{{.RestartCount}}' "$name") cgroup dir present=$(present "$cg") inode=$(stat -c %i "$cg" 2>&1)"
  out "after restart: container cgroup memory.events: $(mev "$cg")"
  system_slice_after
  finish
  probe_last
}

M3() {
  local avail alloc
  record_env
  sync; echo 3 > /proc/sys/vm/drop_caches
  avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
  alloc=$(( avail * 6 / 10 ))
  echo "MemAvailable=${avail}MiB subject_alloc=${alloc}MiB subject_limit=$((alloc + 128))MiB filler_max=$(( avail * 8 / 10 ))MiB" | tee "$dir/sizing.txt"
  run_subject --memory "$((alloc + 128))m" "$IMG" alloc "$alloc" 16 0 0
  wait_log "$name" holding 30
  at_start
  docker run -d --name "$name-filler" --entrypoint /filler "$IMG" alloc $(( avail * 8 / 10 )) 4 10 0 >/dev/null
  local t0; t0=$(now)
  while running "$name" && running "$name-filler"; do
    [ "$(el "$t0" | cut -d. -f1)" -ge 90 ] && { echo "timeout: both still running" | tee -a "$dir/notes.txt"; break; }
    sleep 0.05
  done
  after_exit
  echo "subject running=$(docker inspect -f '{{.State.Running}}' "$name") filler running=$(docker inspect -f '{{.State.Running}}' "$name-filler")" | tee -a "$dir/sizing.txt"
  docker kill "$name-filler" >/dev/null 2>&1
  system_slice_after
  finish
  probe_last
}

M4() {
  record_env
  run_subject --memory 128m "$IMG" alloc 16 16 0 300 /marker
  wait_log "$name" holding 10
  at_start
  echo "\$ kill -9 $pid1" > "$dir/action.txt"
  kill -9 "$pid1" >> "$dir/action.txt" 2>&1
  timeout 10 docker wait "$name" > "$dir/wait.txt"
  after_exit
  system_slice_after
  finish
  probe_last
}

fail() { echo "FAILED $cond: $*" | tee -a "$results/failures.txt"; failed=$((failed + 1)); }
expect() { grep -qE "$1" "$dir/summary.txt" || fail "summary.txt has no line matching '$1'"; }

for c in "$@"; do
  begin "$c" 1
  "$c"
  [ -s "$dir/notes.txt" ] && fail "notes: $(tr '\n' ' ' < "$dir/notes.txt")"
  for x in inspect-start.json inspect-end.json events.jsonl kernel.txt summary.txt memevents.txt; do
    [ -s "$dir/$x" ] || fail "missing $x"
  done
  case $c in
    M1) expect '^ExitCode: 137$'; expect '^constraint: constraint=CONSTRAINT_MEMCG'; expect '^memcg_vs_container: oom_memcg==container' ;;
    M2) expect '^RestartCount: 1$'; expect '^Running: true$'; expect '^constraint: constraint=CONSTRAINT_MEMCG'; expect '^memcg_vs_container: oom_memcg==container' ;;
    M3) expect '^ExitCode: 137$'; expect '^constraint: constraint=CONSTRAINT_NONE global_oom task_memcg=[^ ]+ task=subject'; expect '^memcg_vs_container: task_memcg==container' ;;
    M4) expect '^ExitCode: 137$'; expect '^oom_record: none$' ;;
  esac
  echo "$c run 1 seconds=$(el "$t_start")" | tee -a "$results/timing.txt"
done
[ "$failed" -eq 0 ] || { echo "$failed check(s) failed"; exit 1; }
