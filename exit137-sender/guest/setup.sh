#!/usr/bin/env bash
# Runs as root inside the test VM: install Docker, build the subject image
# (static subject binary + busybox, no registry pull) and record versions.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-/root/results}
mkdir -p "$results"
# Keep apt-daily, apt-daily-upgrade and unattended-upgrades from running
# during the conditions (memory use in a 512 MiB VM, package changes).
systemctl stop apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service unattended-upgrades.service 2>/dev/null || true
systemctl mask apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service unattended-upgrades.service >/dev/null 2>&1
export DEBIAN_FRONTEND=noninteractive
apt_opts=(-q -o DPkg::Lock::Timeout=300)
export NEEDRESTART_SUSPEND=1
rm -f /var/lib/man-db/auto-update
apt_opts+=(-o Acquire::Languages=none)
el() { awk -v a="$(date +%s.%N)" -v b="$1" 'BEGIN{printf "%.1f", a-b}'; }
t0=$(date +%s.%N)
apt-get "${apt_opts[@]}" update
echo "apt-get update seconds: $(el "$t0")"
t0=$(date +%s.%N)
. "$here/versions.env"
apt-get "${apt_opts[@]}" install -y --no-install-recommends \
  docker.io="$DOCKER_IO_VERSION" containerd="$CONTAINERD_VERSION" runc="$RUNC_VERSION"
apt-mark hold docker.io containerd runc
systemctl enable --now docker
if [ "${TRACE:-0}" = 1 ]; then
  apt-get "${apt_opts[@]}" install -y --no-install-recommends bpftrace
fi
if [ "${LOCKSHIM:-0}" = 1 ]; then
  apt-get "${apt_opts[@]}" install -y --no-install-recommends vmtouch
fi
if [ "${DEBUG:-0}" = 1 ]; then
  # debug logs for containerd (and its shims) and dockerd
  mkdir -p /etc/systemd/system/containerd.service.d
  printf '[Service]\nExecStart=\nExecStart=/usr/bin/containerd --log-level debug\n' > /etc/systemd/system/containerd.service.d/debug.conf
  echo '{"debug": true}' > /etc/docker/daemon.json
  systemctl daemon-reload
  systemctl restart containerd docker
fi
echo "apt-get install docker.io seconds: $(el "$t0")"

rootfs=$(mktemp -d)
mkdir -p "$rootfs/bin"
install -m 0755 "$here/subject" "$rootfs/subject"
install -m 0755 "$here/subject" "$rootfs/filler"
install -m 0755 "$here/busybox" "$rootfs/bin/busybox"
for a in sh kill sleep cat ls ps; do ln -s busybox "$rootfs/bin/$a"; done
tar -C "$rootfs" -c . | docker import -c 'ENTRYPOINT ["/subject"]' - exit137/subject:local
rm -rf "$rootfs"

set +e
{
  echo "\$ uname -r"; uname -r
  echo "\$ cat /etc/os-release | grep PRETTY"; grep PRETTY /etc/os-release
  echo "\$ cat /sys/fs/cgroup/cgroup.controllers"; cat /sys/fs/cgroup/cgroup.controllers
  echo "\$ free -m"; free -m
  echo "\$ swapon --show"; swapon --show
  echo "\$ docker version"; docker version
  echo "\$ containerd --version"; containerd --version
  echo "\$ runc --version"; runc --version
  echo "\$ docker-init --version"; docker-init --version
  echo "\$ readlink -f \$(command -v docker-init)"; readlink -f "$(command -v docker-init)"
  echo "\$ docker info (selected)"
  docker info --format 'CgroupDriver={{.CgroupDriver}} CgroupVersion={{.CgroupVersion}} InitBinary={{.InitBinary}} InitCommit={{json .InitCommit}} ContainerdCommit={{json .ContainerdCommit}} RuncCommit={{json .RuncCommit}}'
  echo "\$ dpkg-query (docker.io containerd runc tini)"
  dpkg-query -W docker.io containerd runc tini 2>&1
  echo "\$ systemctl is-active/is-enabled apt-daily* unattended-upgrades"
  for u in apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service unattended-upgrades.service; do
    echo "$u $(systemctl is-active $u) $(systemctl is-enabled $u)"
  done
  echo "\$ systemctl show -p OOMScoreAdjust docker containerd systemd-journald ssh"
  for u in docker containerd systemd-journald ssh; do echo "$u $(systemctl show -p OOMScoreAdjust --value $u)"; done
} > "$results/versions.txt" 2>&1
cat "$results/versions.txt"
