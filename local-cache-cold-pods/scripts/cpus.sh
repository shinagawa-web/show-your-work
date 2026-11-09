#!/usr/bin/env bash
set -euo pipefail
mapfile -t cores < <(cat /sys/devices/system/cpu/cpu[0-9]*/topology/thread_siblings_list | sort -u | sort -t, -k1,1n -k1,1)
half=$(( ${#cores[@]} / 2 ))
join() { local IFS=,; echo "$*"; }
echo "NODE_CPUS=$(join "${cores[@]:0:$half}")"
echo "PG_CPUS=$(join "${cores[@]:$half}")"
