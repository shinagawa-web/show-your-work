# exit137-sender

Records what the kernel log, `docker events`, `docker inspect` and the Docker daemon's journal show when a container exits with 137, for each way the container is stopped. The conditions are defined in `guest/conditions.sh`.

Everything runs inside a small test VM started with QEMU+KVM, so that a host-wide out-of-memory condition stays inside it.

## Run

On an Ubuntu 24.04 host with `/dev/kvm` (this is what CI does). `run-all.sh` first runs `scripts/install-deps.sh`, which installs QEMU and the build tools with apt and adds a udev rule on that host that makes `/dev/kvm` mode 0666. `scripts/kvm-preflight.sh` only checks that KVM works and the test VM boots, and writes `results/preflight/`.

```
cd exit137-sender
./run-all.sh          # all conditions
./run-all.sh C1 C2    # some of them
```

Raw output goes to `results/`, one directory per condition and run. `results/summary.md` has one row per run.

## Pinned versions

`scripts/versions.env` pins docker.io, containerd and runc in the test VM, and the Ubuntu cloud image it boots.
