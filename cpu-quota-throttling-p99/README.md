# cpu-quota-throttling-p99

Verification code for [shinagawa-web/sre-consulting-plan#364](https://github.com/shinagawa-web/sre-consulting-plan/issues/364). It reproduces, in a container with a CPU limit (CFS quota), the symptom where only p99 jumps while the 60s-average utilization stays low, and measures it from outside the server.

## Layout

| Path | Role |
|---|---|
| `server/` | Server under test. W workers accept a connection, use `CPU_MS` of CPU time, return an empty 200 and close. It records nothing |
| `loadgen/` | Load tool. Send times are fixed in advance and requests are sent without waiting for responses, one connection per request. It records only send/receive times and errors |
| `scripts/run.sh` | Building block that runs one condition |
| `scripts/analyze.py` | Building block that aggregates one condition and writes `summary.json` |
| `scripts/report.py` | Extracts fields (`show`) and builds a comparison table (`table`) from `summary.json` |
| `scripts/collector.py` | Reads the cgroup's `cpu.stat` / `cpu.pressure` and `/proc/pressure/cpu` every 10ms and 1s |
| `scripts/recvq.py` | Reads rx_queue (the same value as `ss -lnt` Recv-Q) from the LISTEN line of `/proc/<pid>/net/tcp` and `tcp6` of the container PID, every 10ms in one process |
| `scripts/throttle.bt` | bpftrace. Records, per CPU, when the cgroup under test is throttled and unthrottled, via kprobes on `throttle_cfs_rq` / `unthrottle_cfs_rq` |
| `scripts/env_probe.sh` | Runs on the host under test for s10: checks where the CPU limit appears in Docker, systemd and k3s, installs and uninstalls k3s, and records host state before and after |
| `scripts/clocksrv.py` | Clock synchronization when the load tool and the host under test are different machines |
| `sections/sN_*.sh` | One script per section of the issue |
| `lima.yaml` | Definition of the Lima VM used locally |

## Section scripts

| Script | What it runs | What it outputs |
|---|---|---|
| `sections/s3_repro.sh` | `--cpus 0.5`, W=4, Poisson RPS 10, seed=1, 60s | `results/s3/` |
| `sections/s4_investigate.sh` | Runs nothing. Reads `results/s3` | Throttle rate, 1s-average utilization, cpu.pressure during throttling, Recv-Q |
| `sections/s5_cause.sh` | RPS 5 / 20 (W=4), W=1 (RPS 10). `DUR` seconds each, default 60 (s3 is always 60s) | `results/s5/<condition>/` and a 4-row comparison table including s3 (with a duration column) |
| `sections/s6_mechanism.sh` | Runs nothing. Reads `results/s3` | Length of one throttle, number of threads using CPU at the same time, number of throttles crossed by requests at or above p99 |
| `sections/s9_verify.sh` | CPU limit 0.75 / 1.0 / 1.5 / 2.0 with W=4, Poisson RPS 10, `DUR` seconds each, default 60 (0.5 is s3) | `results/s9/<condition>/` and a 5-row comparison table including s3 |
| `sections/s10_env.sh` | No load. Starts an idle `--cpus 0.5` Docker container, a transient systemd service and scope with `CPUQuota=50%`, and (unless `SKIP_K3S=1`) installs k3s, runs a Pod with `resources.limits.cpu: 500m`, then uninstalls k3s | `results/s10/env.jsonl` and a table of cgroup path, `cpu.max` and `cpu.stat` read on the host and inside the container / Pod |

s4, s5, s6 and s9 stop if `results/s3` does not exist.

## Switching the execution environment

| Environment variable | Default | Meaning |
|---|---|---|
| `EXEC` | `limactl shell cpu-quota-throttling-p99 --` | Prefix for running commands on the host under test. If empty (`EXEC=`), commands run directly on the current host |
| `SAME_HOST` | `0` | `1` means the load tool and the container under test are on the same host. Clock synchronization (`clocksrv.py`) is not used and the offset is taken as 0 |
| `SEED` / `BURST` / `CPU_MS` | `1` / `1` / `20` | Random seed for arrivals, number of requests per burst, CPU time per request |
| `DUR` | `60` | Length in seconds of each condition in s5 and s9 (s3 always runs 60s) |
| `SKIP_K3S` | `0` | `1` skips the k3s part of s10 |

Lima (default):

```
limactl start --name cpu-quota-throttling-p99 lima.yaml
sections/s3_repro.sh
```

The load comes from the macOS side through Lima's port forwarding (127.0.0.1:18080).

Directly on the host under test (CI, etc.):

```
EXEC= SAME_HOST=1 sections/s3_repro.sh
```

## Requirements on the host under test

- Linux, cgroup v2 (`stat -fc %T /sys/fs/cgroup` prints `cgroup2fs`)
- Docker. The cgroup path is taken from `/proc/<pid>/cgroup`, so either the systemd or the cgroupfs driver works
- `bpftrace` (kernel with BTF, `/sys/kernel/btf/vmlinux` exists) and permission to attach kprobes
- `iproute2` (`ss`, used to wait for startup), `util-linux` (`nsenter`), `python3`
- Passwordless `sudo` (used for bpftrace, nsenter and reading `/proc/<pid>/net`)
- Go on the side that runs the load tool (`run.sh` builds `loadgen/loadgen` if it is missing)

On Ubuntu:

```
sudo apt-get install -y docker.io bpftrace iproute2 python3
```

If the `cqt-server` image is missing, `run.sh` builds it from `server/`.

## k3s for section 10

`sections/s10_env.sh` installs k3s on the host under test, checks one Pod, and removes k3s again, so the host goes back to Docker only. Set `SKIP_K3S=1` to skip this part.

What `scripts/env_probe.sh` does on the host:

1. Install: `curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable traefik --disable servicelb --disable metrics-server" sh -`, then wait until the node is `Ready` and the `default` service account exists
2. Apply a Pod `cqt-env` (`alpine:3.19`, `sleep 600`, `resources.limits.cpu: 500m`) with `k3s kubectl`, wait for `Ready`
3. Find the container PID with `k3s crictl inspect`, take the cgroup path from `/proc/<pid>/cgroup`, read `cpu.max` and `cpu.stat` there and in its parent (Pod) cgroup, and read `/sys/fs/cgroup/cpu.max` and `cpu.stat` inside the Pod with `k3s kubectl exec`
4. Delete the Pod, run `/usr/local/bin/k3s-uninstall.sh`, then stop the leftover `kubepods.slice` systemd units and remove `/etc/rancher` and `/var/lib/rancher`, which the uninstall script leaves behind
5. Record the host state after uninstall: `docker ps -a`, whether Docker is active, remaining k3s processes, systemd units and unit files, binaries in `/usr/local/bin`, `kubepods` cgroups and slice units, CNI / flannel / veth links, iptables rules mentioning kube / cni / flannel, and `/var/lib/rancher` / `/etc/rancher`

The host needs outbound HTTPS to get.k3s.io and the image registries.

## CI

`.github/workflows/cpu-quota-throttling-p99.yml` (at the repository root) runs the sections on `ubuntu-24.04` with `EXEC=`, `SAME_HOST=1` and `DUR=60`, one job per section:

| Job | Needs | What it does | Artifact |
|---|---|---|---|
| `preflight` | - | Checks cgroup v2, the Docker cgroup driver, bpftrace with BTF, and that kprobes attach to `throttle_cfs_rq` / `unthrottle_cfs_rq` | `cpu-quota-throttling-p99-preflight` |
| `s3` | preflight | Runs section 3 | `cpu-quota-throttling-p99-s3` |
| `s4`, `s6` | s3 | Download the s3 artifact and only aggregate it | `cpu-quota-throttling-p99-s4`, `-s6` |
| `s5`, `s9` | preflight | Run the s3 baseline again on the same runner first, then the section, so the comparison table only mixes values from one machine | `cpu-quota-throttling-p99-s5`, `-s9` |
| `s10` | preflight | Installs and removes k3s on its own runner | `cpu-quota-throttling-p99-s10` |

Every job that measures runs the same checks first (`.github/actions/cpu-quota-throttling-p99-preflight`) and writes `results/runner_<section>.txt` with the host name, CPU model, CPU count, kernel and cgroup driver. The comparison tables also show the host name and CPU model of each run.
