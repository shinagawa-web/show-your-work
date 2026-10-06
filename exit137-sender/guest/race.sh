#!/usr/bin/env bash
# Runs as root inside the test VM after setup.sh. Steps, in order:
#   C1 / C2      one run of the condition
#   RACE_ON      start recording kernel events into one ftrace buffer
#   RACE_OFF     stop recording
#   PLAIN        (first step only) do not run `ctr events`
#   TP_ON        record only the tracepoints mark_victim, sched_process_exit
#                (subject), cgroup_notify_populated and cgroup_rmdir, with the
#                default buffer size; the buffer is drained after every run
#   BUFFER       only enlarge the ftrace buffer (buffer_size_kb=16384), no events
# Always: `ctr -n moby events` in the background (containerd's own record of
# /tasks/oom), and per-run conditions (leftover containers and scopes,
# MemAvailable) in pre.txt. The ftrace buffer uses trace_clock=boot; clock.txt
# has CLOCK_REALTIME/CLOCK_BOOTTIME pairs to put user-space times on it.
set -uo pipefail
here=$(dirname "$0")
. "$here/conditions.sh"
t=/sys/kernel/tracing
clockpair() { python3 -c 'import time; print(time.clock_gettime_ns(time.CLOCK_REALTIME), time.clock_gettime_ns(time.CLOCK_BOOTTIME))'; }

env_record() {
  local e=$results/env; mkdir -p "$e"
  cp /proc/cpuinfo "$e/cpuinfo.txt"
  cat /sys/devices/system/clocksource/clocksource0/current_clocksource > "$e/clocksource.txt"
  cat /sys/devices/system/clocksource/clocksource0/available_clocksource >> "$e/clocksource.txt"
  cat /proc/cmdline > "$e/cmdline.txt"
  sysctl -a > "$e/sysctl.txt" 2>/dev/null
  systemctl show > "$e/systemd-manager.txt"
  systemctl cat docker containerd docker.socket > "$e/systemd-units.txt" 2>&1
  cat /etc/docker/daemon.json > "$e/docker-daemon.json" 2>&1
  docker info > "$e/docker-info.txt" 2>&1
  containerd config dump > "$e/containerd-config.toml" 2>&1
  dpkg-query -W > "$e/packages.txt"
}

race_on() {
  echo 0 > $t/tracing_on
  echo > $t/trace
  echo boot > $t/trace_clock
  echo 1 > $t/options/record-tgid
  echo 16384 > $t/buffer_size_kb
  echo > $t/kprobe_events
  echo 'p:racep/open do_sys_openat2 name=+0($arg2):ustring' >> $t/kprobe_events
  echo 'r:racep/open_ret do_sys_openat2 ret=$retval:s32' >> $t/kprobe_events
  echo 'p:racep/kn_workfn kernfs_notify_workfn' >> $t/kprobe_events
  for e in racep/open racep/open_ret; do echo 'comm ~ "containerd-shim*"' > $t/events/$e/filter; done
  echo 'comm == "subject"' > $t/events/sched/sched_process_exit/filter
  for e in racep/open racep/open_ret racep/kn_workfn oom/mark_victim sched/sched_process_exit \
           cgroup/cgroup_notify_populated cgroup/cgroup_rmdir; do echo 1 > $t/events/$e/enable; done
  echo "on $(clockpair)" >> "$results/clock.txt"
  echo 1 > $t/tracing_on
}
tp_on() {
  echo 0 > $t/tracing_on
  echo > $t/trace
  echo boot > $t/trace_clock
  echo 1 > $t/options/record-tgid
  echo 'comm == "subject"' > $t/events/sched/sched_process_exit/filter
  for e in oom/mark_victim sched/sched_process_exit cgroup/cgroup_notify_populated cgroup/cgroup_rmdir; do
    echo 1 > $t/events/$e/enable
  done
  echo "on $(clockpair)" >> "$results/clock.txt"
  echo 1 > $t/tracing_on
}
race_off() {
  echo 0 > $t/tracing_on
  echo "off $(clockpair)" >> "$results/clock.txt"
  cat $t/trace >> "$results/ftrace.txt"
  echo 0 > $t/events/enable
  echo > $t/kprobe_events
}

env_record
ctr_pid=
if [ "${1:-}" = PLAIN ]; then
  shift
else
  ctr -n moby events > "$results/ctr-events.txt" 2>&1 &
  ctr_pid=$!
  sleep 0.5
fi
mode=off
declare -A seen
for step in "$@"; do
  case $step in
    RACE_ON) race_on; mode=on; continue ;;
    RACE_OFF) race_off; mode=off; continue ;;
    TP_ON) tp_on; mode=tp; continue ;;
    BUFFER) echo 16384 > $t/buffer_size_kb; continue ;;
  esac
  seen[$step]=$(( ${seen[$step]:-0} + 1 ))
  begin "$step" "${seen[$step]}"
  {
    echo "trace=$mode"
    echo "containers=$(docker ps -aq | wc -l)"
    echo "docker_scopes=$(ls -d /sys/fs/cgroup/system.slice/docker-*.scope 2>/dev/null | wc -l)"
    grep MemAvailable /proc/meminfo
    [ "$mode" = tp ] || echo "clock $(clockpair)"
  } > "$dir/pre.txt"
  "$step"
  if [ "$mode" = tp ]; then
    cat $t/trace >> "$results/ftrace.txt"; echo > $t/trace
  else
    echo "clock $(clockpair)" >> "$dir/pre.txt"
  fi
  echo "$step run ${seen[$step]} trace=$mode seconds=$(el "$t_start")" | tee -a "$results/timing.txt"
done
[ "$mode" = on ] && race_off
if [ "$mode" = tp ]; then
  for f in buffer_size_kb tracing_on kprobe_events set_event; do echo "== $f"; cat $t/$f; done > "$results/ftrace-state.txt" 2>&1
  echo 0 > $t/tracing_on
  echo "off $(clockpair)" >> "$results/clock.txt"
  cat $t/trace >> "$results/ftrace.txt"
  echo 0 > $t/events/enable
  mode=off
fi
[ -n "$ctr_pid" ] && { sleep 0.5; kill "$ctr_pid"; }
[ -f "$results/ftrace-state.txt" ] || for f in buffer_size_kb tracing_on kprobe_events set_event; do
  echo "== $f"; cat $t/$f
done > "$results/ftrace-state.txt" 2>&1
