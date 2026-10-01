#!/usr/bin/env bash
set -uo pipefail
out=$1
fail=0
warn() { echo "preflight: WARNING: $*" >&2; }
die() { echo "preflight: ERROR: $*" >&2; fail=1; }

[ "$(uname -s)" = Linux ] || die "Linux is required (on macOS, run inside a VM; see README)"
fs=$(stat -fc %T /sys/fs/cgroup 2>/dev/null)
[ "$fs" = cgroup2fs ] || die "cgroup v2 is required (/sys/fs/cgroup is '$fs')"
command -v docker >/dev/null || die "docker is required"
dcg=$(docker info --format '{{.CgroupVersion}}' 2>/dev/null)
[ "$dcg" = 2 ] || die "Docker must run on cgroup v2 (got '$dcg')"
for c in python3 sudo nsenter ss; do command -v "$c" >/dev/null || die "$c is required"; done
sudo -n true 2>/dev/null || die "passwordless sudo is required (nsenter, /proc/<pid>/net, bpftrace)"

bpf=0
bpf_note="bpftrace not found"
if command -v bpftrace >/dev/null; then
  if [ ! -e /sys/kernel/btf/vmlinux ]; then
    bpf_note="no BTF (/sys/kernel/btf/vmlinux)"
  elif ! grep -qE ' (throttle_cfs_rq|unthrottle_cfs_rq)$' /proc/kallsyms; then
    bpf_note="throttle_cfs_rq / unthrottle_cfs_rq not in /proc/kallsyms"
  elif sudo -n timeout 30 "$(command -v bpftrace)" -e 'kprobe:throttle_cfs_rq { } kprobe:unthrottle_cfs_rq { } BEGIN { exit(); }' >/dev/null 2>&1; then
    bpf=1
    bpf_note="kprobes attach to throttle_cfs_rq / unthrottle_cfs_rq"
  else
    bpf_note="bpftrace could not attach kprobes"
  fi
fi
[ "$bpf" = 1 ] || warn "bpftrace unavailable ($bpf_note); kprobe-based figures will be skipped"

mkdir -p "$(dirname "$out")"
{
  echo "host=$(hostname)"
  echo "cpu_model=$(lscpu 2>/dev/null | sed -n 's/^Model name: *//p' | head -1)"
  echo "nproc=$(nproc)"
  echo "kernel=$(uname -r)"
  echo "os=$(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
  echo "cgroup_fs=$fs"
  echo "docker=$(docker version --format '{{.Server.Version}}' 2>/dev/null)"
  echo "docker_cgroup_driver=$(docker info --format '{{.CgroupDriver}}' 2>/dev/null)"
  echo "docker_cgroup_version=$dcg"
  echo "bpftrace=$(bpftrace --version 2>/dev/null || echo none)"
  echo "bpftrace_usable=$bpf"
  echo "bpftrace_note=$bpf_note"
  grep -E '^CONFIG_(HZ|CFS_BANDWIDTH|PREEMPT_DYNAMIC)=' "/boot/config-$(uname -r)" 2>/dev/null
  echo "cpu_pressure=$(tr '\n' ' ' < /proc/pressure/cpu)"
} > "$out"
cat "$out" >&2
[ "$fail" = 0 ] || exit 1
echo "$bpf"
