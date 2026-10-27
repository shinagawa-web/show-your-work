# memory-limit-refault-before-oom

This reproduces, in a container with a memory limit and no swap, the state where a FastAPI server keeps running without an OOM kill while its executable pages are reclaimed and read back, and records the latency seen by a client outside the container together with the cgroup's memory counters.

## Run

`.github/workflows/memory-limit-refault-before-oom.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

| Job | What it runs |
|---|---|
| `s3-<condition>` | `./run-all.sh s3:<condition>` for `p1`, `p3` and `r1`: a baseline, then the condition (`p3` runs alone) |
| `summary` | Merges the results of the other jobs into `results/summary.md` |

Each `s3-<condition>` job writes its own `results/summary.md` to its job summary and uploads `results/` as the `memory-limit-refault-before-oom-s3-<condition>` artifact. The `summary` job writes the merged `results/summary.md` to its job summary, uploads it as the `memory-limit-refault-before-oom-summary` artifact, and on a push posts it as a comment on the open pull request for the branch, replacing the earlier one. `run-all.sh` with no arguments runs every condition in the table below.

## What one run does

`scripts/run.sh` drops the page cache, starts the server from `app/` with `--memory` and `--memory-swap` set to the same value, warms it up with load for 5 s, clears the referenced bits of the server process and loads it for 10 s more, reads `/proc/<pid>/smaps`, then loads it for the measured window while `scripts/collector.py` reads the container's cgroup files every second. The load comes from `loadgen/`, which sends requests at fixed intervals without waiting for responses and measures each one from its scheduled time.

Headroom is `memory.max - (memory.current - file)`, taken from the same 1 s sample. In `p3`, the edge is the first 1 s sample in which `pgmajfault` grew by more than 100.

## Conditions

Each job starts with a probe run under a large limit to measure the memory the server uses outside the page cache, and sets `--memory` from it so that the baseline has the headroom given in `run-all.sh`. The other conditions are sized from the headroom measured in the baseline of the same job:

| Condition | What changes from the baseline |
|---|---|
| `n1` | A file larger than the limit is read with `dd` inside the container before the measured window |
| `p1` | More in-process cache, so that the headroom drops to the P1 value in `run-all.sh`. If p99 does not exceed 1.5 times the baseline p99, or more than 1% of requests time out, the headroom is moved by 0.25 MiB and P1 runs again, up to the number of tries in `run-all.sh`; `results/s3/p1/p1_tries.txt` lists each try |
| `p2` | `p1` with `/sys/kernel/mm/lru_gen/enabled` switched before the container starts, and switched back afterwards |
| `p3` | The in-process cache grows at a fixed rate under load from the baseline, up to an OOM kill or 180 s |
| `f1` | `p1` with `--memory` raised by the extra cache |
| `r1` | `p1` with `read_ahead_kb` of the device under the Docker root directory set to the value in `run-all.sh` before the container starts, and set back afterwards |

## Pinned versions

The image in `app/Dockerfile`, the Python packages in `app/requirements.txt`, and Go in `loadgen/go.mod`.
