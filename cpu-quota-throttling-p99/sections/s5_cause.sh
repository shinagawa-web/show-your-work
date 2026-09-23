#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
claim "issue #364 section 5 (cause): whether throttling hits is decided not by utilization but by the number of threads using CPU at the same time (run RPS 5 / 20 (W=4) and W=1 (RPS 10) for DUR seconds each (default 60) and compare with the 60s s3)"
need_s3
conds=(
  "rps5_w4 0.5 4 5"
  "rps20_w4 0.5 4 20"
  "rps10_w1 0.5 1 10"
)
dirs=("$results/s3")
for c in ${conds[@]+"${conds[@]}"}; do
  read -r name cpus w rps <<< "$c"
  run "$results/s5/$name" "$cpus" "$w" "$rps" poisson "${DUR:-60}"
  analyze "$results/s5/$name"
  dirs+=("$results/s5/$name")
done
report table "${dirs[@]}"
