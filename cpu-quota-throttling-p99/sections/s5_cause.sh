#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
claim "issue #364 section 5 (cause): whether throttling hits is decided not by utilization but by the number of threads using CPU at the same time (run RPS 5 / 20 (W=4) and W=1 (RPS 10) for DUR seconds each (default 60); the table compares them with s3)"
CONDS=("${S5_CONDS[@]}")
run_conds s5 "${1:-}"
[ -n "${1:-}" ] || "$root/sections/table.sh" s5
