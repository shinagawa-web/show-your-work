#!/usr/bin/env bash
# Step 1: what can be built on this host.
#   - device-side limits: null_blk (memory_backed, mbps, completion_nsec), dm-delay
#   - OS-side limit: cgroup v2 io controller and io.max
# Every command is echoed with its full output and exit code to results/probe.log.
# Devices that were created successfully are listed in results/devices.env
# for measure.sh. Must run as root.
set -u
OUT=${OUT:-results}
mkdir -p "$OUT"
LOG="$OUT/probe.log"
ENV="$OUT/devices.env"
: > "$LOG"
: > "$ENV"

run() {
  echo "+ $1" | tee -a "$LOG"
  bash -c "$1" 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[0]}
  echo "rc=$rc" | tee -a "$LOG"
  return "$rc"
}
note() { echo "## $*" | tee -a "$LOG"; }

NB=/sys/kernel/config/nullb

# make_nullb <name> <attr=value>... : create a null_blk device through configfs.
# Attributes are written in order; power=1 is written last.
make_nullb() {
  local name=$1; shift
  run "mkdir $NB/$name" || return 1
  local kv
  for kv in "$@"; do
    run "echo ${kv#*=} > $NB/$name/${kv%%=*}" || return 1
  done
  run "echo 1 > $NB/$name/power" || return 1
  run "cat $NB/$name/index; ls -l /dev/nullb\$(cat $NB/$name/index)" || return 1
  local idx; idx=$(cat "$NB/$name/index")
  echo "/dev/nullb$idx"
}

note "host"
run 'uname -a'
run 'head -n 4 /etc/os-release'
run 'nproc; free -m'

note "cgroup"
run 'mount | grep -E "cgroup|configfs"'
run 'stat -fc %T /sys/fs/cgroup'
run 'cat /sys/fs/cgroup/cgroup.controllers'
run 'cat /sys/fs/cgroup/cgroup.subtree_control'
run 'grep -E "BLK_DEV_THROTTLING|BLK_CGROUP=|BLK_DEV_NULL_BLK|DM_DELAY|BLK_DEV_LOOP=" /boot/config-$(uname -r)'

note "null_blk: modprobe before installing anything"
if ! run 'modprobe null_blk nr_devices=0'; then
  note "null_blk: install linux-modules-extra-\$(uname -r) and retry"
  run 'DEBIAN_FRONTEND=noninteractive apt-get install -y -q linux-modules-extra-$(uname -r) 2>&1 | tail -n 5'
  run 'modprobe null_blk nr_devices=0'
fi
run 'modinfo null_blk | grep -E "^(filename|vermagic|parm:\s+(mbps|memory_backed|completion_nsec|irqmode|nr_devices))"'
run 'mountpoint /sys/kernel/config || mount -t configfs none /sys/kernel/config'
run "cat $NB/features"

note "null_blk: module-parameter route (separate load, then unload)"
run 'modprobe -r null_blk'
run 'modprobe null_blk nr_devices=1 memory_backed=1 mbps=4 irqmode=2 completion_nsec=1000000'
run 'ls -l /dev/nullb*; grep . /sys/module/null_blk/parameters/{mbps,memory_backed,irqmode,completion_nsec} 2>&1'
run 'modprobe -r null_blk'
run 'modprobe null_blk nr_devices=0'

note "null_blk: configfs devices"
# base: no delay, no limit (used for unlimited and io.max runs, and under dm-delay)
if d=$(make_nullb base size=1024 blocksize=4096 memory_backed=1 | tail -n 1) && [ -b "$d" ]; then
  echo "DEV_BASE=$d" >> "$ENV"
fi
# completion: every request completes after 1 ms (timer completion)
if d=$(make_nullb compl size=1024 blocksize=4096 memory_backed=1 irqmode=2 completion_nsec=1000000 | tail -n 1) && [ -b "$d" ]; then
  echo "DEV_COMPL=$d" >> "$ENV"
fi
# mbps: device-side bandwidth cap of 4 MiB/s (= 1024 x 4 KiB/s)
if d=$(make_nullb mbps size=1024 blocksize=4096 memory_backed=1 mbps=4 | tail -n 1) && [ -b "$d" ]; then
  echo "DEV_MBPS=$d" >> "$ENV"
fi
run 'ls -l /dev/nullb*'
run 'for d in /sys/block/nullb*; do echo "$d sched=$(cat $d/queue/scheduler) nr_requests=$(cat $d/queue/nr_requests)"; done'

note "dm-delay: modprobe"
if ! run 'modprobe dm-delay'; then
  run 'DEBIAN_FRONTEND=noninteractive apt-get install -y -q linux-modules-extra-$(uname -r) 2>&1 | tail -n 5'
  run 'modprobe dm-delay'
fi
run 'dmsetup targets'

note "dm-delay: on top of a null_blk device (its own lower device, not shared)"
if d=$(make_nullb dmlow size=1024 blocksize=4096 memory_backed=1 | tail -n 1) && [ -b "$d" ]; then
  echo "DEV_DMLOW=$d" >> "$ENV"
  if run "dmsetup create delay1ms --table \"0 \$(blockdev --getsz $d) delay $d 0 1\""; then
    run 'dmsetup table delay1ms; ls -l /dev/mapper/delay1ms; readlink -f /dev/mapper/delay1ms'
    echo "DEV_DMDELAY=$(readlink -f /dev/mapper/delay1ms)" >> "$ENV"
  fi
fi

note "dm-delay: on top of a loop device (alternative if null_blk is missing)"
run 'truncate -s 256M /tmp/loopback.img && losetup -f --show --direct-io=on /tmp/loopback.img'
LOOP=$(losetup -j /tmp/loopback.img | cut -d: -f1 | head -n 1)
if [ -n "$LOOP" ]; then
  run "dmsetup create delayloop --table \"0 \$(blockdev --getsz $LOOP) delay $LOOP 0 1\" && dmsetup table delayloop"
  run 'dmsetup remove delayloop'
  run "losetup -d $LOOP"
fi

note "cgroup v2 io controller and io.max"
run 'grep -qw io /sys/fs/cgroup/cgroup.subtree_control || echo +io > /sys/fs/cgroup/cgroup.subtree_control; cat /sys/fs/cgroup/cgroup.subtree_control'
run 'mkdir -p /sys/fs/cgroup/iotest && ls /sys/fs/cgroup/iotest | grep -E "^io\."'
BASE=$(grep '^DEV_BASE=' "$ENV" | cut -d= -f2)
if [ -n "$BASE" ]; then
  MM=$(lsblk -ndo MAJ:MIN "$BASE" | tr -d ' ')
  run "echo '$MM riops=1000' > /sys/fs/cgroup/iotest/io.max && cat /sys/fs/cgroup/iotest/io.max"
  run "echo '$MM riops=max' > /sys/fs/cgroup/iotest/io.max && cat /sys/fs/cgroup/iotest/io.max"
fi
note "devices.env"
cat "$ENV" | tee -a "$LOG"
