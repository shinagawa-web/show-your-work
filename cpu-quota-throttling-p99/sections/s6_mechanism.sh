#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
claim "issue #364 section 6 (mechanism): a stopped request is delayed by the rest of the period, and how many times it crosses that decides p99 (length of one throttle, number of threads using CPU at the same time, crossings by requests at or above p99)"
need_s3
report show "$results/s3" kprobe throttled_usec_per_nr_throttled cpus_in_use_10ms requests_crossing_throttle slow_ge_p99 hist_latency_10ms
