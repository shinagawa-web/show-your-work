#!/usr/bin/env bash
# Step 2: fio 4 KiB O_DIRECT random reads under three kinds of limit,
# with iostat -x sampled on the same devices during the run.
#   a        no limit                         (DEV_BASE)
#   b        cgroup v2 io.max riops=1000      (DEV_BASE, MAJ:MIN of DEV_BASE)
#   c_compl  null_blk completion_nsec=1ms     (DEV_COMPL)
#   c_mbps   null_blk mbps=4                  (DEV_MBPS)
#   c_dm     dm-delay 1 ms on a null_blk      (DEV_DMDELAY, lower DEV_DMLOW)
# fio always runs inside /sys/fs/cgroup/iotest; io.max is set only for b.
# Must run as root, after probe.sh.
set -u
OUT=${OUT:-results}
RUNTIME=${RUNTIME:-10}
IOSTAT_WIN=${IOSTAT_WIN:-8}
DEPTHS=${DEPTHS:-"1 16"}
CG=/sys/fs/cgroup/iotest
# shellcheck disable=SC1091
source "$OUT/devices.env"

mm() { lsblk -ndo MAJ:MIN "$1" | tr -d ' '; }

# one <cond> <depth> <fio device> <iostat devices...>
one() {
  local cond=$1 qd=$2 dev=$3; shift 3
  local tag="${cond}_qd${qd}"
  local names=()
  local x
  for x in "$@"; do names+=("$(basename "$x")"); done
  echo "=== $tag fio=$dev iostat=${names[*]} io.max=$(tr '\n' ' ' < $CG/io.max)"
  bash -c "echo \$\$ > $CG/cgroup.procs && exec fio --name=$tag --filename=$dev \
      --rw=randread --bs=4k --direct=1 --ioengine=libaio --iodepth=$qd \
      --time_based --runtime=$RUNTIME --norandommap --randrepeat=0 \
      --lat_percentiles=1 --clat_percentiles=1 \
      --output-format=json --output=$OUT/fio_$tag.json" &
  local pid=$!
  sleep 1
  iostat -dxy "${names[@]}" "$IOSTAT_WIN" 1 > "$OUT/iostat_$tag.txt"
  wait "$pid"
  echo "fio rc=$?"
  cat "$OUT/iostat_$tag.txt"
  cat $CG/io.stat > "$OUT/cgroup_io.stat_$tag.txt"
}

for qd in $DEPTHS; do
  [ -n "${DEV_BASE:-}" ] && one a "$qd" "$DEV_BASE" "$DEV_BASE"
  if [ -n "${DEV_BASE:-}" ]; then
    echo "$(mm "$DEV_BASE") riops=1000" > $CG/io.max
    one b "$qd" "$DEV_BASE" "$DEV_BASE"
    echo "$(mm "$DEV_BASE") riops=max" > $CG/io.max
  fi
  [ -n "${DEV_COMPL:-}" ] && one c_compl "$qd" "$DEV_COMPL" "$DEV_COMPL"
  [ -n "${DEV_MBPS:-}" ] && one c_mbps "$qd" "$DEV_MBPS" "$DEV_MBPS"
  [ -n "${DEV_DMDELAY:-}" ] && one c_dm "$qd" "$DEV_DMDELAY" "$DEV_DMDELAY" "$DEV_DMLOW"
done
true
