# exit137-sender

A container exits with 137. This records, per SIGKILL sender (the kernel OOM killer, Docker, another process on the host) and per way of ending (PID 1 killed by SIGKILL, PID 1 returning 137), what shows up in `docker events`, the kernel log (OOM killer records), `docker inspect` and dockerd's journal.

Everything runs inside a small test VM (512 MiB, no swap, cgroup v2) started with QEMU+KVM, so the host-wide out-of-memory condition stays inside it.

| Layer | Local | CI |
|---|---|---|
| Linux | lima VM (vz, aarch64) on macOS | GitHub Actions `ubuntu-24.04` runner (x86_64) |
| Test VM | QEMU+KVM inside the lima VM | QEMU+KVM on the runner |

## Run

On the CI runner, or any Ubuntu 24.04 host with `/dev/kvm`:

```
cd exit137-sender
./run-all.sh            # all conditions
./run-all.sh C1 C2      # some of them
```

`run-all.sh` installs QEMU, builds the subject, boots the test VM, installs Docker in it (`guest/setup.sh`), runs `guest/conditions.sh` there and copies the raw output back to `results/`. `results/summary.md` has one row per run. `kvm-preflight.sh` only checks `/dev/kvm` and boots the test VM.

Locally on Apple Silicon (M3 or later, macOS 15 or later), start the Linux layer with nested virtualization and run the same script inside it:

```
limactl start --name=exit137 --tty=false exit137-sender/lima.yaml
limactl copy -r exit137-sender exit137:/tmp/
limactl shell exit137 -- /tmp/exit137-sender/run-all.sh
```

## Conditions

| | Sender | How it ends | Runs |
|---|---|---|---|
| C1 | kernel, container limit (`--memory 128m`, subject allocates 256 MiB) | PID 1 killed | 3 |
| C2 | kernel, host-wide (subject holds 60% of MemAvailable; an unlimited filler grows until memory runs out) | PID 1 killed | 10 |
| C3 | Docker, `docker stop -t 2` | PID 1 killed | 1 |
| C4 | Docker, `docker kill` | PID 1 killed | 1 |
| C5 | host process, `kill -9` to PID 1 | PID 1 killed | 1 |
| C6 | kernel, container limit, `--init` | PID 1 (tini) returns 137 | 3 |
| C7 | host process, `kill -9` to tini's child | PID 1 (tini) returns 137 | 1 |
| C8 | none, subject returns 137 | PID 1 returns 137 | 1 |
| C9 | `docker exec ... kill -9 1` from inside the container | | 1 |
| C10 | C1 with `--restart on-failure`, allocating only on the first start | | 1 |
| C11 | C4 with `--restart on-failure` | | 1 |

Conditions run more than once where the outcome can differ between runs: the order of the `oom` and `die` events (C1, C2, C6) and, in C2, which process the OOM killer picks and whether `oom`/`OOMKilled` show up. The others are run once. Set `RUNS_<COND>` to change it.

The subject (`subject/subject.c`) is a single static process without signal handlers that allocates and holds memory, or returns a given code. The image holds it as `/subject` and as `/filler` (C2's unlimited container), plus busybox for `kill` in C9. It is imported from a tarball, so nothing is pulled from a registry.

Per run, `results/<COND>/<run>/` holds `events.jsonl`, `kernel.txt` (`journalctl -k`), `dockerd.txt`, `containerd.txt`, `inspect-start.json` (right after start: `State.Pid`, `HostConfig.Init`), `start.txt` (PID 1 and its children), `inspect-end.json` and `summary.txt`. `results/versions.txt` has the kernel, containerd, runc, Docker and docker-init versions; `results/timing.txt` the seconds per run and per condition.

## Scripts

- `scripts/install-deps.sh` installs QEMU, firmware, cloud-image-utils, gcc and busybox-static for the host architecture and makes `/dev/kvm` accessible to the current user
- `scripts/kvm-check.sh` prints `/dev/kvm` and the kernel's KVM messages, then calls `KVM_GET_API_VERSION` and `KVM_CREATE_VM`
- `scripts/vm-up.sh` boots the Ubuntu 24.04 cloud image with QEMU+KVM and a cloud-init seed, and waits until a command runs over SSH
- `scripts/vm-ssh.sh` runs a command in the test VM
- `scripts/vm-down.sh` stops the test VM
- `guest/setup.sh` (in the test VM) installs `docker.io`, imports the subject image and records versions
- `guest/conditions.sh` (in the test VM) runs C1 to C11
- `guest/summarize.py` turns the raw files into `summary.txt` per run and `summary.md`

`vm-up.sh` picks by `uname -m`: on x86_64 it uses `qemu-system-x86_64 -machine q35` and the amd64 image; on aarch64 it uses `qemu-system-aarch64 -machine virt,gic-version=host` with AAVMF firmware and the arm64 image. Everything else is shared.
