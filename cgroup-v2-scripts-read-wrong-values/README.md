# cgroup-v2-scripts-read-wrong-values

This starts four Pods in a kind cluster on a cgroup v2 host (a busy loop under a CPU limit, a Pod that writes and reads a file, a Pod that runs out of memory and restarts, and a Pod with a 1 CPU request) and records the cgroup v2 files that scripts written for cgroup v1 read: `cpu.stat`, `memory.stat`, `memory.current`, `memory.events`, `memory.events.local`, `memory.oom.group` and `cpu.weight`, from inside the container, from the node and from `kubectl debug node`, next to the kubelet's `/metrics/resource`.

## Run

`.github/workflows/cgroup-v2-scripts-read-wrong-values.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

`results/summary.md` goes to the job summary, with a table of whether the data for each prediction was collected and whether the prediction held (checked by `scripts/checks.py`), and every raw output under `results/` is uploaded as the `cgroup-v2-scripts-read-wrong-values-results` artifact. A run that did not collect the data (a command that read a prediction's values exited non-zero or printed nothing, `scripts/p4.sh` exited non-zero, the cache Pod did not finish reading its file or its `memory.current` did not settle, the oom Pod did not reach the restart count, a failed step) fails the job with a `RUN INVALID` line at the top of the summary; a prediction that did not hold never fails it.

## Outputs

| File | What it records |
|---|---|
| `00-environment.txt` | Kernel, `/sys/kernel/mm/lru_gen/enabled`, kind, Kubernetes, containerd and runc versions, the cgroup2 mount |
| `01-symptom.txt` | The v1 controller directories on the node and inside a container |
| `p1-cpu-stat-inside-vs-node.txt` | `cpu.stat` read on the node, through `kubectl exec`, and on the node again |
| `p2-throttled-key.txt` | `cpu.stat` of the busy loop, grepped for the v1 and v2 keys |
| `p3-working-set.txt` | `memory.current` and `memory.stat` of the container scope and the Pod slice, then `*_memory_working_set_bytes` from `/metrics/resource` |
| `p4-oom-kill.txt` | `memory.events`, `memory.events.local` and `memory.oom.group` of the container scope and the Pod slice each time the container state changes, the restart count, and the kernel log of the kills |
| `p5-cpu-weight.txt` | `cpu.weight` of the Pod slice and the container scope |
| `p6-node-debug-cgroup.txt` | `/proc/<pid>/cgroup` of a container read from the node and from a `kubectl debug node` Pod, and the container's scope found under `/host/sys/fs/cgroup` by its ID with its `cgroup.procs` (`scripts/node-debug.sh`) |

The Pods are in `k8s/pods.yaml`; `run-all.sh` fills in the busybox image from `scripts/versions.env` when it applies them. The OOM Pod sleeps before it allocates, so its container scope can be read after each restart; `scripts/p4.sh` stops at the restart count set in `RESTARTS`.

## Pinned versions

`scripts/versions.env`.
