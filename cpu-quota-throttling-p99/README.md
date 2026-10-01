# cpu-quota-throttling-p99

Verification code for [shinagawa-web/sre-consulting-plan#364](https://github.com/shinagawa-web/sre-consulting-plan/issues/364). It reproduces, in a container with a CPU limit (CFS quota), the symptom where only p99 jumps while the 60s-average utilization stays low, and measures it from outside the server.

## Quick start

On a Linux host (cgroup v2, Docker):

```
cd cpu-quota-throttling-p99
./run-all.sh
```

This runs every section and writes `results/summary.md`. Everything runs on the same host: the container under test, the load generator and the collectors.

## Entry point

```
./run-all.sh [target ...]
```

| Target | What it runs |
|---|---|
| `s3` | Section 3 (repro): `--cpus 0.5`, W=4, Poisson RPS 10 |
| `s4`, `s6` | Sections 4 and 6: read `results/s3` (run s3 first if it is missing) |
| `s5` or `s5:<name>` | Section 5 (cause): all conditions, or one of `rps5_w4`, `rps20_w4`, `rps10_w1` |
| `s9` or `s9:<name>` | Section 9 (verification): all conditions, or one of `cpus0.75_w4`, `cpus1.0_w4`, `cpus1.5_w4`, `cpus2.0_w4` |
| `s10` | Section 10 (environments): where the CPU limit appears in Docker and systemd, and in k3s with `K3S=1` |

With no target it runs `s3 s4 s5 s6 s9 s10`. Examples:

```
DUR=10 ./run-all.sh s3
./run-all.sh s5:rps10_w1 s9:cpus1.0_w4
K3S=1 ./run-all.sh s10
```

| Environment variable | Default | Meaning |
|---|---|---|
| `DUR` | `60` | Seconds of load per condition |
| `K3S` | `0` | `1` installs k3s in s10, checks one Pod with `resources.limits.cpu: 500m`, then uninstalls k3s |
| `RESULTS` | `results` | Output directory |
| `SEED` / `BURST` / `CPU_MS` | `1` / `1` / `20` | Random seed for arrivals, requests per burst, CPU time per request |

Each run first calls `scripts/preflight.sh`, which stops the run unless Linux, cgroup v2, Docker on cgroup v2, `python3`, `nsenter`, `ss` and passwordless `sudo` are available, and writes `results/environment/<host>_<targets>.txt`. At the end, `scripts/report.py summary results` writes `results/summary.md` from every result found under `results/`, so results from several runs (or several machines) can be merged into one directory and summarized again.

### bpftrace is optional

If `bpftrace` is installed, BTF is present and kprobes attach to `throttle_cfs_rq` / `unthrottle_cfs_rq`, the run records when the container is throttled and unthrottled on each CPU. Otherwise preflight prints a warning and the run continues: the kprobe-based figures (length of one throttle, throttles crossed per request) show `skipped (bpftrace unavailable)`, and the other figures come from `cpu.stat`, `cpu.pressure` and the listen socket. Section 4 then takes throttle intervals from 10ms `cpu.stat` samples instead of kprobes.

### Per-period analysis

With bpftrace, `scripts/analyze.py` also cuts the run into CFS periods on the container's `sched_cfs_period_timer` firings and adds to `summary.json`:

- `kprobe`: throttle length per CPU and for the union of CPUs (median, mean, max, sum), the `throttled_usec` increase over the run, and whether each throttle ended by the next period timer firing
- `period_arrivals`: per period, requests sent by the load generator, carry-over from earlier periods and whether the container was throttled
- `period_usage`: `usage_usec` from the 10ms `cpu.stat` samples summed into 100ms windows, aligned to the period boundaries and at other phases, against the `nr_throttled` increase

In `period_usage`, the `aligned` windows take usage from the samples nearest to each period boundary and the `nr_throttled` increase from the first samples at or after boundary + 2ms. The kernel adds to `nr_throttled` in the period timer at the end of the throttled period. The collector stamps each sample after reading `cpu.stat`, so a sample stamped a fraction of a millisecond after the boundary can hold a value read before the timer ran and put the increase in the next window. The other window sets (`naive`, `offset_sweep`) read both counters from the same samples.

`results/summary.md` shows these for `s3` and `s5/rps10_w1`. The same numbers, with per-period detail and a CSV next to the results, come from:

```
python3 scripts/period_arrivals.py results/s3 [carry_threshold_ms]
python3 scripts/period_usage.py results/s3 [near_ms]
```

## Linux

Requirements on the host:

- cgroup v2 (`stat -fc %T /sys/fs/cgroup` prints `cgroup2fs`)
- Docker on cgroup v2 (either the systemd or the cgroupfs driver)
- `python3`, `iproute2` (`ss`), `util-linux` (`nsenter`), passwordless `sudo`
- Optional: `bpftrace` with a BTF kernel (`/sys/kernel/btf/vmlinux`)
- Go is optional: if `go` is not on `PATH`, `scripts/build.sh` builds the load generator in a `golang:1.25` container

On Ubuntu:

```
sudo apt-get install -y docker.io python3 iproute2 bpftrace
sudo usermod -aG docker "$USER"
```

Then log in again and run `./run-all.sh`.
