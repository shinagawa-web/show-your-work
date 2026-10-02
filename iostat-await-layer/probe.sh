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
  run 'DEBIAN_FRONTEND=noninteractive apt-get install -y -q linux-modules-extra-$(uname -r) 2>&1 | grep -vE "^(Reading|Building|Get:|Fetched|Selecting|Preparing|Unpacking|Scanning|No (containers|user|VM)|Running kernel|Restarting)" | head -n 20'
  run 'dpkg -l "linux-modules-extra-$(uname -r)" | tail -n 1'
  run 'find /lib/modules/$(uname -r) -name "null_blk*" -o -name "scsi_debug*" -o -name "dm-delay*" | sort'
  run 'modprobe null_blk nr_devices=0'
fi
run 'modinfo null_blk | grep -E "^(filename|vermagic|parm:\s+(mbps|memory_backed|completion_nsec|irqmode|nr_devices))"'
run 'mountpoint /sys/kernel/config || mount -t configfs none /sys/kernel/config'
run "ls $NB/ && cat $NB/features"

note "null_blk: module-parameter route"
run 'modprobe null_blk nr_devices=1 memory_backed=1 mbps=4 irqmode=2 completion_nsec=1000000'
run 'ls -l /dev/nullb*'

note "null_blk: configfs route"
make_nullb probe size=256 blocksize=4096 memory_backed=1 irqmode=2 completion_nsec=1000000 mbps=4
run 'ls -l /dev/nullb*'

note "base device for a/b: loop on a tmpfs file"
run 'grep -E "SCSI_DEBUG|BLK_DEV_UBLK" /boot/config-$(uname -r)'
run 'dd if=/dev/urandom of=/dev/shm/base.img bs=1M count=256 status=none && losetup -f --show --direct-io=on /dev/shm/base.img'
L=$(losetup -j /dev/shm/base.img | cut -d: -f1 | head -n 1)
[ -b "$L" ] && echo "DEV_BASE=$L" >> "$ENV"
run "losetup -l $L"

note "dm-delay: modprobe"
run 'modprobe dm-delay'
run 'dmsetup targets'

note "dm-delay 1 ms (read and write) on its own loop device"
run 'dd if=/dev/urandom of=/dev/shm/dmlow.img bs=1M count=256 status=none && losetup -f --show --direct-io=on /dev/shm/dmlow.img'
L2=$(losetup -j /dev/shm/dmlow.img | cut -d: -f1 | head -n 1)
if [ -b "$L2" ]; then
  echo "DEV_DMLOW=$L2" >> "$ENV"
  if run "dmsetup create delay1ms --table \"0 \$(blockdev --getsz $L2) delay $L2 0 1\""; then
    run 'dmsetup table delay1ms; readlink -f /dev/mapper/delay1ms'
    echo "DEV_DMDELAY=$(readlink -f /dev/mapper/delay1ms)" >> "$ENV"
  fi
fi

note "scsi_debug: ndelay=1 ms per command (device-side delay below the block layer)"
if run 'modprobe scsi_debug dev_size_mb=256 sector_size=4096 ndelay=1000000 max_luns=1 num_tgts=1'; then
  run 'sleep 2; ls /sys/bus/pseudo/drivers/scsi_debug/adapter0/host*/target*/*/block/'
  SD=$(ls /sys/bus/pseudo/drivers/scsi_debug/adapter0/host*/target*/*/block/ 2>/dev/null | head -n 1)
  if [ -n "$SD" ] && [ -b "/dev/$SD" ]; then
    echo "DEV_SDEBUG=/dev/$SD" >> "$ENV"
    run "grep . /sys/bus/pseudo/drivers/scsi_debug/{ndelay,delay,max_queue}; cat /sys/block/$SD/device/queue_depth"
  fi
fi

run 'for d in /sys/block/{loop,dm-,sd,nullb}*; do [ -e $d/queue/scheduler ] && echo "$d sched=$(cat $d/queue/scheduler) nr_requests=$(cat $d/queue/nr_requests)"; done; true'

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
