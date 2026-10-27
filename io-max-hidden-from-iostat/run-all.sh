#!/usr/bin/env bash
set -uo pipefail
usage() {
  cat >&2 <<'USAGE'
usage: ./run-all.sh [target ...]

targets (default: s3 s4 s5):
  s3[:name]   section 3: baseline, io.max riops=1000 (3 runs), batched reads, rotational 0 and 1
  s4[:name]   section 4: systemd, Docker, libaio, dm, blk tracer and blktrace
  s5[:name]   section 5: fewer concurrent reads, other riops values

Conditions are listed in conditions.tsv. Runs as root (re-executes itself with sudo).
env: RUNTIME=10 seconds of fio per condition
The run ends by writing results/summary.md.
USAGE
}
case "${1:-}" in -h|--help) usage; exit 0 ;; esac
[ "$(id -u)" = 0 ] || exec sudo -E "$0" "$@"
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
results=$here/results
targets=("$@")
[ ${#targets[@]} -gt 0 ] || targets=(s3 s4 s5)

rows() { awk -F'\t' -v s="$1" -v n="$2" 'NR > 1 && $1 == s && (n == "" || $2 == n)' conditions.tsv; }
for t in "${targets[@]}"; do
  sec=${t%%:*} only=
  [ "$t" = "$sec" ] || only=${t#*:}
  case "$sec" in s3|s4|s5) ;; *) echo "unknown target: $t" >&2; usage; exit 2 ;; esac
  [ -n "$(rows "$sec" "$only")" ] || { echo "unknown condition: $t" >&2; exit 2; }
done

rm -rf "$results"
label=$(printf '%s_' "${targets[@]}" | tr ':' '-')
host=$results/host/${label%_}
mkdir -p "$host"
start_all=$(date +%s)
tools=(fio iostat pidstat blktrace blkparse)
missing() { local c; for c in "${tools[@]}"; do command -v "$c" > /dev/null || printf '%s ' "$c"; done; }
if [ -n "$(missing)" ]; then
  echo "missing before install: $(missing)" > "$host/install.log"
  { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q fio sysstat blktrace; } >> "$host/install.log" 2>&1
  if [ -n "$(missing)" ]; then
    tail -20 "$host/install.log"
    echo "RUN INVALID: still missing after apt-get install: $(missing)" | tee "$results/summary.md"
    exit 1
  fi
fi
{
  echo "kernel: $(uname -r)"
  grep PRETTY_NAME /etc/os-release
  echo "nproc: $(nproc)"
  grep -E '^CONFIG_HZ=|^CONFIG_BLK_DEV_THROTTLING=|^CONFIG_PSI=|^CONFIG_PSI_DEFAULT_DISABLED' "/boot/config-$(uname -r)"
  fio --version
  iostat -V 2>&1 | head -1
  blktrace -V 2>&1 | head -1
  docker version --format 'docker {{.Server.Version}}' 2>&1
  systemctl --version | head -1
  echo "cgroup.controllers: $(cat /sys/fs/cgroup/cgroup.controllers)"
} > "$host/environment.txt" 2>&1

sysctl -qw kernel.task_delayacct=1
echo "kernel.task_delayacct: $(sysctl -n kernel.task_delayacct)" >> "$host/environment.txt"
grep -qw io /sys/fs/cgroup/cgroup.subtree_control || echo +io > /sys/fs/cgroup/cgroup.subtree_control
mkdir -p /sys/fs/cgroup/iomax-test

declare -A DEV TARGET IODEVS FIRSTROT
cleanup() {
  mountpoint -q /mnt/iomax-p9 && umount /mnt/iomax-p9
  dmsetup remove iomax-lin 2>/dev/null
  for f in /dev/shm/iomax-*.img; do
    [ -e "$f" ] && losetup -j "$f" | cut -d: -f1 | xargs -r -n1 losetup -d
  done
  rm -f /dev/shm/iomax-*.img
  rmdir /sys/fs/cgroup/iomax-test 2>/dev/null
  [ -n "${SUDO_UID:-}" ] && chown -R "$SUDO_UID:$SUDO_GID" "$results"
}
trap cleanup EXIT

mkloop() {
  local f=/dev/shm/iomax-$1.img l
  dd if=/dev/urandom of="$f" bs=1M count="$2" status=none
  losetup -f --show --direct-io=on "$f"
}
record_dev() {
  printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$(lsblk -ndo MAJ:MIN "$2" | tr -d ' ')" \
    "$(cat /sys/block/$(basename "$2")/queue/rotational)" "$(losetup -nO DIO "$2" 2>/dev/null | tr -d ' ')" >> "$host/devices.tsv"
}
ensure_dev() {
  local d=$1 l dm
  [ -n "${DEV[$d]:-}" ] && return
  case "$d" in
    main)
      l=$(mkloop main 256); DEV[$d]=$l; TARGET[$d]=$l; IODEVS[$d]=$(basename "$l") ;;
    rot0|rot1)
      l=$(mkloop "$d" 256)
      echo "${d#rot}" > "/sys/block/$(basename "$l")/queue/rotational"
      DEV[$d]=$l; TARGET[$d]=$l; IODEVS[$d]=$(basename "$l") ;;
    dm)
      l=$(mkloop p9 384)
      dmsetup create iomax-lin --table "0 $(blockdev --getsz "$l") linear $l 0"
      dm=$(basename "$(readlink -f /dev/mapper/iomax-lin)")
      mkfs.ext4 -q -F /dev/mapper/iomax-lin
      mkdir -p /mnt/iomax-p9 && mount /dev/mapper/iomax-lin /mnt/iomax-p9
      dd if=/dev/urandom of=/mnt/iomax-p9/data bs=1M count=256 oflag=direct status=none
      DEV[$d]=$l; TARGET[$d]=/mnt/iomax-p9/data; IODEVS[$d]="$dm $(basename "$l")"
      record_dev "$d-dm" "/dev/$dm" ;;
  esac
  record_dev "$d" "$l"
}
printf 'name\tdevice\tmajmin\trotational_at_create\tdirect_io\n' > "$host/devices.tsv"
printf 'section\tname\tseconds\n' > "$host/timing.tsv"

docker_ready=0
rc=0
for t in "${targets[@]}"; do
  sec=${t%%:*} only=
  [ "$t" = "$sec" ] || only=${t#*:}
  sec_start=$(date +%s)
  while IFS=$'\t' read -r -u 3 _ name engine numjobs iodepth riops via device pattern trace; do
    c_start=$(date +%s)
    ensure_dev "$device"
    tdev=${DEV[$device]}
    if [ "$via" = docker ] && [ "$docker_ready" = 0 ]; then
      docker build -q -t iomax-fio docker > "$host/docker_build.log" 2>&1 || { echo "RUN INVALID: docker build failed" >&2; rc=1; continue; }
      docker run --rm iomax-fio --version >> "$host/docker_build.log" 2>&1
      docker_ready=1
    fi
    rot_now=$(cat "/sys/block/$(basename "$tdev")/queue/rotational")
    if [ "$riops" != max ] && [ -z "${FIRSTROT[$tdev]:-}" ]; then
      FIRSTROT[$tdev]=$rot_now
    fi
    echo "== $sec $name: $engine numjobs=$numjobs iodepth=$iodepth riops=$riops via=$via device=$device($tdev) rotational=$rot_now pattern=$pattern trace=$trace" >&2
    out=$results/$sec/$name
    "$here/scripts/run_cond.sh" "$out" "$name" "$engine" "$numjobs" "$iodepth" "$riops" "$via" \
      "${TARGET[$device]}" "$tdev" "$pattern" "$trace" "${IODEVS[$device]}" 100
    printf 'rotational_at_first_iomax\t%s\n' "${FIRSTROT[$tdev]:-}" >> "$out/settings.txt"
    python3 "$here/scripts/analyze.py" "$out" > "$out/analysis.txt" 2>&1 || rc=1
    grep -h 'RUN INVALID' "$out/analysis.txt" "$out/invalid.txt" 2>/dev/null >&2 && rc=1
    printf '%s\t%s\t%s\n' "$sec" "$name" "$(( $(date +%s) - c_start ))" >> "$host/timing.tsv"
  done 3< <(rows "$sec" "$only")
  printf '%s\t%s\t%s\n' "$sec" "total" "$(( $(date +%s) - sec_start ))" >> "$host/timing.tsv"
done
printf '%s\t%s\t%s\n' "all" "total" "$(( $(date +%s) - start_all ))" >> "$host/timing.tsv"

python3 scripts/summary.py "$results" "${targets[@]}" > "$results/summary.md" || rc=1
cat "$results/summary.md"
exit $rc
