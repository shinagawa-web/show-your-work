# glibc-heap-not-returned

Runs a Python program on the official `python` image (Debian, glibc) that repeatedly creates 100 objects of 1 MiB, holds them, drops them and waits, and records the resident memory of the process, the size of its `[heap]` mapping, and what glibc's `mallinfo2()` reports, cycle by cycle. In each cycle the program also creates one small object after the peak and keeps it until the end. After the last cycle, two conditions run `malloc_trim(0)` inside the running process with `sys.remote_exec`, called from a process started in the same container with `docker exec`.

The conditions are in `conditions.tsv`: the size of the object kept in each cycle (`0` for none), an environment variable passed to the container, whether the program calls `malloc_trim(0)` after each release, whether `malloc_trim(0)` is injected after the last cycle, and the capability added to the container with `--cap-add` (`-` for none).

## Run

`.github/workflows/glibc-heap-not-returned.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. Each job runs one condition from `conditions.tsv` per matrix entry with `./run-all.sh <name>`, which starts three runs of it at the same time, each in its own container. A job is named after the section of the article whose numbers it produces first; the same runs also give the numbers for the later sections.

| Job | What it runs |
|---|---|
| `s3 (p1-default)` | glibc defaults, with `malloc_trim(0)` injected after the last cycle into a container with `--cap-add=SYS_PTRACE` |
| `s3 (p2-no-keep)` | No object kept after the peak |
| `s3 (p3-mmap-threshold)` | `MALLOC_MMAP_THRESHOLD_=131072` |
| `s3 (p4-trim-threshold)` | `MALLOC_TRIM_THRESHOLD_=131072` |
| `s3 (p5-trim-after-release)` | `malloc_trim(0)` called by the program after each release |
| `s4 (p6-exec-no-cap)` | `p1-default` without `--cap-add` |
| `report` | Merges the results of the other jobs into `results/summary.md` |

`results/summary.md` goes to the job summary of each job, with one part per section of the article. Each value is the median of the three runs of a condition, and the summary states the largest difference between runs. Each job uploads `results/` as an artifact named `glibc-heap-not-returned-s<section>-<name>`, and the `report` job writes the merged summary to its job summary, to a comment on the open pull request for the branch (the same comment is overwritten on every run), and to the `glibc-heap-not-returned-summary` artifact. A job fails when a run did not complete all cycles, when the injected script did not run in a condition with `--cap-add`, or when the object kept in cycle 2 is not between the last peak object and the end of `[heap]`.

## What is recorded

Each run writes `results/<name>/run<k>/`:

- `settings.txt`: the image, `docker run` options and the arguments of `app/observer.py`
- `env.txt`: the glibc version, `uname -m`, page size, Python version, whether the GIL is enabled, `kernel.yama.ptrace_scope`, the effective capabilities, the `MALLOC_` variables, `LD_PRELOAD`, `PYTHONMALLOC` and the content of `/etc/ld.so.preload` seen in the container
- `events.txt`: written by `app/workload.py` as it runs. For each cycle: the duration of the peak stage, the difference in `ru_minflt` (minor page faults) across the peak stage, and the `id()` of the first and last peak object and of the kept object; `arena`, `uordblks`, `fordblks` and `hblkhd` from `mallinfo2()` at the end of the hold and at the end of the wait; `VmRSS` read by the process just before and just after `gc.collect()`; the duration of `malloc_trim(0)` when the program calls it. Also the time the injected `app/trim.py` ran and how long `malloc_trim(0)` took there
- `samples.tsv`: `VmRSS` from `/proc/<pid>/status`, `Size` and `Rss` of `[heap]` from `/proc/<pid>/smaps` and the end address of `[heap]` from `/proc/<pid>/maps`, read every 0.5 s by `app/observer.py`
- `inject.txt`: written by `app/inject.py`. When `sys.remote_exec` was called, and whether it returned without an error or the type and message of the exception it raised
- `inject-stdout.txt`: the output of `docker exec`
- `container.txt`: the exit code and wall time of the container

`app/observer.py` starts `app/workload.py` as its child process and samples it. When the workload has been idle for a few seconds, `scripts/run.sh` runs `app/inject.py` in the same container with `docker exec`, which calls `sys.remote_exec` with the workload's process ID in the container to run `app/trim.py`. The sizes, cycle count and times are set in `scripts/run.sh`.

## Pinned versions

`scripts/versions.env`.
