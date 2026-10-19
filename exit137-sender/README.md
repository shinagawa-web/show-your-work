# exit137-sender

Records what the kernel log, `docker events`, `docker inspect` and the Docker daemon's journal show when a container exits with 137, for each way the container is stopped. The conditions are defined in `guest/conditions.sh`.

For four of those ways (a container memory limit with and without a restart policy, a host-wide out-of-memory condition, and `kill -9` from the host), it also records the cgroup v2 `memory.events` of the container's cgroup and of `system.slice`, and whether the container's cgroup still exists after the exit. These scenarios are defined in `guest/memevents.sh`.

Everything runs inside a small test VM started with QEMU+KVM, so that a host-wide out-of-memory condition stays inside it.

## Run

`.github/workflows/exit137-sender.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

The workflow has two jobs.

- `run` runs every condition in `guest/conditions.sh`. `results/summary.md`, one row per run, goes to the job summary, and `results/`, one directory per condition and run, is uploaded as the `exit137-sender-results` artifact.
- `memory-events` runs `./run-all.sh memory-events`: each scenario in `guest/memevents.sh` runs once, right after a reboot of the test VM (`scripts/vm-reboot.sh`), so every scenario starts from a freshly booted VM. `results/summary.md` goes to the job summary, with the `memory.events` readings listed under the table, and `results/` is uploaded as the `exit137-sender-results-memory-events` artifact. The job fails when a scenario did not end the way it is set up to (for example, no out-of-memory kill of the container where one is caused).

## Pinned versions

`scripts/versions.env`.
