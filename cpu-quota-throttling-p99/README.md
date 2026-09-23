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

## macOS (Lima)

macOS has no cgroups, so run the same command inside a Linux VM. `lima.yaml` is one way to get one (Apple Silicon, `vz`, 4 vCPUs, Ubuntu 24.04 with Docker, python3, iproute2 and bpftrace). Mount this directory into the VM, writable, at the same path:

```
cd cpu-quota-throttling-p99
limactl start --name cpu-quota-throttling-p99 --mount "$PWD:w" lima.yaml
limactl shell cpu-quota-throttling-p99 -- bash -c "cd '$PWD' && ./run-all.sh"
```

Results appear in `results/` on the macOS side through the mount. For an existing VM, add the mount with `limactl stop`, `limactl edit <vm> --mount "$PWD:w"` and `limactl start`.

## CI

`.github/workflows/cpu-quota-throttling-p99.yml` (at the repository root) calls `run-all.sh` with `DUR=60` on `ubuntu-24.04`. All jobs except `report` start at the same time, each on its own runner:

| Job | Command | Artifact |
|---|---|---|
| `s3` | `./run-all.sh s3 s4 s6` | `cpu-quota-throttling-p99-s3` |
| `s5` (matrix) | `./run-all.sh s5:<condition>` | `cpu-quota-throttling-p99-s5-<condition>` |
| `s9` (matrix) | `./run-all.sh s9:<condition>` | `cpu-quota-throttling-p99-s9-<condition>` |
| `s10` | `K3S=1 ./run-all.sh s10` | `cpu-quota-throttling-p99-s10` |
| `report` (`if: always()`) | Downloads every artifact into `results/`, runs `scripts/report.py summary results` and appends `summary.md` to the job summary | `cpu-quota-throttling-p99-summary` |

`.github/actions/cpu-quota-throttling-p99-setup` only installs Go, bpftrace, iproute2 and python3; the checks are in `scripts/preflight.sh`. Matrix jobs use `fail-fast: false`. Because s3 and each condition run on different runners, the comparison tables show the host name and CPU model of every row.

## Layout

| Path | Role |
|---|---|
| `run-all.sh` | Entry point |
| `conditions.tsv` | Conditions for s3, s5 and s9 (section, name, cpus, workers, rps) |
| `server/` | Server under test. W workers accept a connection, use `CPU_MS` of CPU time, return an empty 200 and close. It records nothing |
| `loadgen/` | Load generator. Send times are fixed in advance and requests are sent without waiting for responses, one connection per request. It records send time, receive time, monotonic latency and errors |
| `scripts/preflight.sh` | Checks the host and records its environment; reports whether bpftrace can be used |
| `scripts/build.sh` | Builds the `cqt-server` image and the load generator (`loadgen/bin/`) if they are missing |
| `scripts/run.sh` | Runs one condition: starts the container with `--cpus`, the collectors and the load generator |
| `scripts/collector.py` | Reads the cgroup's `cpu.stat` / `cpu.pressure` and `/proc/pressure/cpu` every 10ms and 1s |
| `scripts/recvq.py` | Reads rx_queue (the same value as `ss -lnt` Recv-Q) from the LISTEN line of `/proc/<pid>/net/tcp` and `tcp6` of the container, every 10ms in one process |
| `scripts/throttle.bt` | bpftrace: records per CPU when the container's cgroup is throttled and unthrottled, via kprobes on `throttle_cfs_rq` / `unthrottle_cfs_rq` |
| `scripts/env_probe.sh` | Section 10: checks where the CPU limit appears in Docker, systemd and k3s, installs and uninstalls k3s, and records host state before and after |
| `scripts/analyze.py` | Aggregates one condition into `summary.json` |
| `scripts/report.py` | Builds `summary.md` (`summary <results_dir>`) and comparison tables (`table <run_dir>...`) |
| `lima.yaml` | One way to get a Linux VM on macOS |

## k3s in section 10

With `K3S=1`, `scripts/env_probe.sh` installs k3s on the host, checks one Pod, and removes k3s again, so the host goes back to Docker only. Without it, s10 checks only Docker and systemd.

1. Install: `curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable traefik --disable servicelb --disable metrics-server" sh -`, then wait until the node is `Ready` and the `default` service account exists
2. Apply a Pod `cqt-env` (`alpine:3.19`, `sleep 600`, `resources.limits.cpu: 500m`) with `k3s kubectl`, wait for `Ready`
3. Find the container PID with `k3s crictl inspect`, take the cgroup path from `/proc/<pid>/cgroup`, read `cpu.max` and `cpu.stat` there and in its parent (Pod) cgroup, and read `/sys/fs/cgroup/cpu.max` and `cpu.stat` inside the Pod with `k3s kubectl exec`
4. Delete the Pod, run `/usr/local/bin/k3s-uninstall.sh`, then stop the leftover `kubepods.slice` systemd units and remove `/etc/rancher` and `/var/lib/rancher`, which the uninstall script leaves behind
5. Record the host state after uninstall: `docker ps -a`, whether Docker is active, remaining k3s processes, systemd units and unit files, binaries in `/usr/local/bin`, `kubepods` cgroups and slice units, CNI / flannel / veth links, iptables rules mentioning kube / cni / flannel, and `/var/lib/rancher` / `/etc/rancher`

The host needs outbound HTTPS to get.k3s.io and the image registries.
