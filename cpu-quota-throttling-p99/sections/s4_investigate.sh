#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
claim "issue #364 section 4 (investigation): there is short-term exhaustion invisible in the 60s average, and the container is stopped by the limit (throttle rate, 1s-average utilization, cpu.pressure during throttling, Recv-Q)"
need_s3
report show "$results/s3" util_window_pct util_1s_pct util_1s_series_pct delta_window throttle_rate pressure recvq
