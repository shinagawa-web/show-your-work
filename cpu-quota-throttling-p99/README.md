# cpu-quota-throttling-p99

This reproduces, in a container with a CPU limit (CFS quota), the symptom where only p99 jumps while the 60s-average utilization stays low, and measures it from outside the server.

## Run

`.github/workflows/cpu-quota-throttling-p99.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. The conditions are in `conditions.tsv`.

| Job | What it runs |
|---|---|
| `s3` | Sections 3, 4 and 6 |
| `s5 (<name>)` | Section 5 (cause), one job per condition |
| `s9 (<name>)` | Section 9 (verification), one job per condition |
| `s10` | Section 10 (environments): where the CPU limit appears in Docker, systemd and k3s |
| `report` | Merges the results of the other jobs into `results/summary.md` |

`results/summary.md` goes to the job summary and is uploaded as the `cpu-quota-throttling-p99-summary` artifact. Each of the other jobs uploads its `results/` as an artifact named after the job.

## bpftrace

Throttling is recorded with kprobes on `throttle_cfs_rq` / `unthrottle_cfs_rq`. If they cannot attach, the run continues without them: the kprobe-based figures show `skipped (bpftrace unavailable)`, and Section 4 takes throttle intervals from `cpu.stat` samples instead.

## Per-period analysis

With bpftrace, `summary.json` also has the run cut into CFS periods on the container's period timer firings: `kprobe` (throttle lengths), `period_arrivals` (requests sent per period and carried over) and `period_usage` (`cpu.stat` usage per 100ms window against the `nr_throttled` increase). In the `aligned` windows of `period_usage`, the `nr_throttled` increase is read 2ms after each period boundary, because the kernel adds to it in the period timer and a sample stamped just after the boundary can hold the value from before the timer ran.

## Pinned versions

The images in `server/Dockerfile` and `scripts/build.sh`, and Go in `loadgen/go.mod`.
