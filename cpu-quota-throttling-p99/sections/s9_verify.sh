#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
claim "issue #364 section 9 (verification, supply side and observation side): raise the CPU limit from 0.5 (s3) to 0.75 / 1.0 / 1.5 / 2.0 with W=4, Poisson RPS 10, DUR seconds each (default 60), to see how p99 and throttle rate change and find the smallest limit that meets the target p99"
CONDS=("${S9_CONDS[@]}")
run_conds s9 "${1:-}"
[ -n "${1:-}" ] || "$root/sections/table.sh" s9
