#!/usr/bin/env bash
# Define uprobe events (tracefs) at instructions in containerd-shim-runc-v2
# 2.2.1-0ubuntu1~24.04.3 (arm64, stripped). Addresses come from .gopclntab and
# objdump of cgroups v3.1.2 (*Manager).EventChan.func1 (0x2894a0) and
# task.(*service).oomEvent (0x34f5a0). File offset = address - 0x10000.
# scripts/find-shim-uprobes.py derives the same addresses from the binary.
set -euo pipefail
bin=/usr/bin/containerd-shim-runc-v2
want=2.2.1-0ubuntu1~24.04.3
[ "$(dpkg-query -W -f '${Version}' containerd)" = "$want" ] || { echo "containerd is not $want" >&2; exit 1; }
t=/sys/kernel/tracing
echo "-:shimp/*" >> $t/uprobe_events 2>/dev/null || true
echo "p:shimp/isempty $bin:0x279580 r0=%x0:u64" >> $t/uprobe_events  # 0x289580
echo "p:shimp/readmev $bin:0x2795f0 r0=%x0:u64" >> $t/uprobe_events  # 0x2895f0
echo "p:shimp/sendev $bin:0x2796bc " >> $t/uprobe_events  # 0x2896bc
echo "p:shimp/retev $bin:0x2796c8 " >> $t/uprobe_events  # 0x2896c8
echo "p:shimp/senderr $bin:0x27977c " >> $t/uprobe_events  # 0x28977c
echo "p:shimp/retsilent $bin:0x279798 " >> $t/uprobe_events  # 0x289798
echo "p:shimp/readerr $bin:0x2797d8 " >> $t/uprobe_events  # 0x2897d8
echo "p:shimp/oomevent $bin:0x33f5a0 " >> $t/uprobe_events  # 0x34f5a0
cat $t/uprobe_events
# Record them in the ftrace buffer together with cgroup population changes,
# cgroup removal and OOM victims, on the boot clock, with the tgid.
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
# realtime minus boottime, in ns, to convert the buffer's timestamps
python3 -c 'import time; print(time.clock_gettime_ns(time.CLOCK_REALTIME) - time.clock_gettime_ns(time.CLOCK_BOOTTIME))'
