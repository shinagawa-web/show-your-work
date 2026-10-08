#!/usr/bin/env bash
# Offsets for this containerd build; regenerate with scripts/find-shim-uprobes.py.
set -euo pipefail
bin=/usr/bin/containerd-shim-runc-v2
want=2.2.1-0ubuntu1~24.04.3
[ "$(dpkg-query -W -f '${Version}' containerd)" = "$want" ] || { echo "containerd is not $want" >&2; exit 1; }
t=/sys/kernel/tracing
echo "-:shimp/*" >> $t/uprobe_events 2>/dev/null || true
echo "p:shimp/isempty $bin:0x279580 r0=%x0:u64" >> $t/uprobe_events
echo "p:shimp/readmev $bin:0x2795f0 r0=%x0:u64" >> $t/uprobe_events
echo "p:shimp/sendev $bin:0x2796bc " >> $t/uprobe_events
echo "p:shimp/retev $bin:0x2796c8 " >> $t/uprobe_events
echo "p:shimp/senderr $bin:0x27977c " >> $t/uprobe_events
echo "p:shimp/retsilent $bin:0x279798 " >> $t/uprobe_events
echo "p:shimp/readerr $bin:0x2797d8 " >> $t/uprobe_events
echo "p:shimp/oomevent $bin:0x33f5a0 " >> $t/uprobe_events
cat $t/uprobe_events
echo 0 > $t/tracing_on
echo > $t/trace
echo boot > $t/trace_clock
echo 1 > $t/options/record-tgid
echo 16384 > $t/buffer_size_kb
echo 1 > $t/events/shimp/enable
echo 1 > $t/events/cgroup/cgroup_notify_populated/enable
echo 1 > $t/events/cgroup/cgroup_rmdir/enable
echo 1 > $t/events/oom/mark_victim/enable
echo 1 > $t/tracing_on
python3 -c 'import time; print(time.clock_gettime_ns(time.CLOCK_REALTIME) - time.clock_gettime_ns(time.CLOCK_BOOTTIME))'
