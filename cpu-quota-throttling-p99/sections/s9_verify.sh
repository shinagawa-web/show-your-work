#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
claim "issue #364 section 9 (verification, supply side and observation side): raise the CPU limit from 0.5 (s3) to 0.75 / 1.0 / 1.5 / 2.0 with W=4, Poisson RPS 10, DUR seconds each (default 60), to see how p99 and throttle rate change and find the smallest limit that meets the target p99"
need_s3
conds=(
  "cpus0.75_w4 0.75 4 10"
  "cpus1.0_w4 1.0 4 10"
  "cpus1.5_w4 1.5 4 10"
  "cpus2.0_w4 2.0 4 10"
)
dirs=("$results/s3")
for c in ${conds[@]+"${conds[@]}"}; do
  read -r name cpus w rps <<< "$c"
  run "$results/s9/$name" "$cpus" "$w" "$rps" poisson "${DUR:-60}"
  analyze "$results/s9/$name"
  dirs+=("$results/s9/$name")
done
report table "${dirs[@]}"
