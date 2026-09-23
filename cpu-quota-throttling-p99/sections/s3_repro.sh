#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
claim "issue #364 section 3 (repro): with --cpus 0.5, W=4, Poisson RPS 10, only p99 jumps while 60s-average utilization stays around 40%"
run "$results/s3" 0.5 4 10 poisson 60
analyze "$results/s3"
report show "$results/s3" cond util_window_pct latency_ms throttle_rate n_errors
