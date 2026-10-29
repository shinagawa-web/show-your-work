# memory-limit-refault-before-oom

This reproduces, in a container with a memory limit and no swap, the state where a FastAPI server keeps running without an OOM kill while its executable pages are reclaimed and read back, and records the latency seen by a client outside the container together with the cgroup's memory counters.

## Run

`.github/workflows/memory-limit-refault-before-oom.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

| Job | What it runs |
|---|---|
| `s3-<condition>` | `./run-all.sh s3:<condition>` for `n1`, `p3`, `p3b` and `f1`: a baseline, then the condition (`p3` and `p3b` run alone, starting from the baseline) |
| `summary` | Merges the results of the other jobs into `results/summary.md` |

Each `s3-<condition>` job writes its own `results/summary.md` to its job summary and uploads `results/` as the `memory-limit-refault-before-oom-s3-<condition>` artifact. The `summary` job writes the merged `results/summary.md` to its job summary, uploads it as the `memory-limit-refault-before-oom-summary` artifact, and on a push posts it as a comment on the open pull request for the branch, replacing the earlier one.

## What one run does

`scripts/run.sh` drops the page cache, starts the server from `app/` with `--memory` and `--memory-swap` set to the same value, warms it up with load for 15 s, then loads it for the measured window while `scripts/collector.py` reads the container's cgroup files every second: `memory.current`, `memory.max`, `anon`, `file`, `file_mapped` and `pgmajfault` from `memory.stat`, `oom_kill` from `memory.events`, `memory.pressure`, `memory.swap.max` and `memory.swap.current`, and `oom_kill` from the parent cgroup's `memory.events`, which outlives the container. The load comes from `loadgen/`, which sends requests at fixed intervals without waiting for responses and measures each one from its scheduled time. A request that times out enters the p99 as the client timeout set in `loadgen/main.go`. `docker inspect` records whether Docker saw an OOM kill.

`scripts/analyze.py` turns one run into `summary.json`, and `scripts/report.py` turns every run under `results/` into `results/summary.md`. Headroom is `memory.max - (memory.current - file)`, taken from the same 1 s sample. In `p3` and `p3b`, the server is taken to have stopped at the first 1 s sample in which `pgmajfault` grew by more than 100. Each addition to the cache in `p3` and `p3b` is printed by the server with its time and the number of elements, and saved to `grow.log` and `grow.json`.

A job fails when `memory.swap.max` is not 0 or `memory.swap.current` was above 0 in any run, and `results/summary.md` then lists it under `RUN INVALID`.

## Conditions

Each job starts with a probe run under a large limit to measure the memory the server uses outside the page cache, and sets `--memory` from it so that the baseline has the headroom given in `run-all.sh`. The rates and amounts below are in `run-all.sh`.

| Condition | Name in `results/summary.md` | What changes from the baseline |
|---|---|---|
| baseline | `<headroom> MiB headroom`, with the headroom set in `run-all.sh` | Nothing. The cache is filled at startup and stops growing |
| `n1` | `page cache filled to the limit` | A file larger than the limit is read with `dd` inside the container before the measured window |
| `p3` | `cache grows on a timer` | Under load, a timer task on the event loop adds to the in-process cache at a fixed rate, up to an OOM kill or 180 s |
| `p3b` | `cache grows per request` | Under load, every Nth request adds one element to the in-process cache, N chosen to give the same average rate as `p3`, up to an OOM kill or 180 s |
| `f1` | `limit raised` | The in-process cache is filled at startup up to the anonymous memory given in `run-all.sh`, and `--memory` is raised by the extra cache |

## Pinned versions

The image in `app/Dockerfile`, the Python packages in `app/requirements.txt`, and Go in `loadgen/go.mod`.
