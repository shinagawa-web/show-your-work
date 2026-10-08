# exit137-sender

Records what the kernel log, `docker events`, `docker inspect` and the Docker daemon's journal show when a container exits with 137, for each way the container is stopped. The conditions are defined in `guest/conditions.sh`.

Everything runs inside a small test VM started with QEMU+KVM, so that a host-wide out-of-memory condition stays inside it.

## Run

`.github/workflows/exit137-sender.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

The workflow runs every condition. `results/summary.md`, one row per run, goes to the job summary, and `results/`, one directory per condition and run, is uploaded as the `exit137-sender-results` artifact.

## Pinned versions

`scripts/versions.env`.
