# cpu-quota-throttling-p99

This reproduces, in a container with a CPU limit (CFS quota), the symptom where only p99 jumps while the 60s-average utilization stays low, and measures it from outside the server.

## Run

`.github/workflows/cpu-quota-throttling-p99.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

| Job | What it runs |
|---|---|
| `s3` | Sections 3, 4 and 6. Section 3 (repro): `--cpus 0.5`, W=4, Poisson RPS 10 |
| `s5 (<name>)` | Section 5 (cause), one job per condition: `rps5_w4`, `rps20_w4`, `rps10_w1` |
| `s9 (<name>)` | Section 9 (verification), one job per condition: `cpus0.75_w4`, `cpus1.0_w4`, `cpus1.5_w4`, `cpus2.0_w4` |
| `s10` | Section 10 (environments): where the CPU limit appears in Docker and systemd, and in k3s |
| `report` | Merges the results of the other jobs and writes `results/summary.md` with `scripts/report.py` |

`results/summary.md` goes to the job summary and is uploaded as the `cpu-quota-throttling-p99-summary` artifact. Each of the other jobs uploads its `results/` as an artifact named after the job.

Each job first runs `scripts/preflight.sh`, which stops the run unless cgroup v2, Docker on cgroup v2, `python3`, `nsenter`, `ss` and passwordless `sudo` are available, and writes `results/environment/<host>_<targets>.txt`.

## bpftrace

The run records when the container is throttled and unthrottled on each CPU with kprobes on `throttle_cfs_rq` / `unthrottle_cfs_rq`. If they cannot attach, preflight prints a warning and the run continues: the kprobe-based figures (length of one throttle, throttles crossed per request) show `skipped (bpftrace unavailable)`, and Section 4 takes throttle intervals from 10ms `cpu.stat` samples instead.

## Per-period analysis

With bpftrace, `scripts/analyze.py` also cuts the run into CFS periods on the container's `sched_cfs_period_timer` firings and adds to `summary.json`:

- `kprobe`: throttle length per CPU and for the union of CPUs (median, mean, max, sum), the `throttled_usec` increase over the run, and whether each throttle ended by the next period timer firing
- `period_arrivals`: per period, requests sent by the load generator, carry-over from earlier periods and whether the container was throttled
- `period_usage`: `usage_usec` from the 10ms `cpu.stat` samples summed into 100ms windows, aligned to the period boundaries and at other phases, against the `nr_throttled` increase

In `period_usage`, the `aligned` windows take usage from the samples nearest to each period boundary and the `nr_throttled` increase from the first samples at or after boundary + 2ms. The kernel adds to `nr_throttled` in the period timer at the end of the throttled period. The collector stamps each sample after reading `cpu.stat`, so a sample stamped a fraction of a millisecond after the boundary can hold a value read before the timer ran and put the increase in the next window. The other window sets (`naive`, `offset_sweep`) read both counters from the same samples.

`results/summary.md` shows these for `s3` and `s5/rps10_w1`.

## Pinned versions

The images in `server/Dockerfile` and `scripts/build.sh`, and Go in `loadgen/go.mod`.
