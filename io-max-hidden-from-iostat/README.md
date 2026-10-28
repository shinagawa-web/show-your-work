# io-max-hidden-from-iostat

Runs fio random reads with O_DIRECT on a loop device backed by a file in `/dev/shm`, with and without a cgroup v2 `io.max` read IOPS limit (`riops`) on that device. For each condition it records the latency of every read from fio, `iostat -x` for the loop device (and the dm device above it where there is one), the `io.stat` and `io.pressure` of the cgroup fio runs in, `pidstat -d` for the fio processes, and, in some conditions, the blk tracer events for the loop device. The limit is set in four ways: by writing `io.max` directly, with `systemd-run -p IOReadIOPSMax=` given a device path or the path of a file on ext4 on dm-linear, and with `docker run --device-read-iops`.

The conditions are in `conditions.tsv`: the section of the article that uses each one, the fio ioengine, threads (`numjobs`) and `iodepth`, the `riops` value (`max` for no limit), how the limit is set, which device is read, whether reads are issued steadily or in batches separated by idle time, and whether the blk tracer or the blktrace command records the run (`ftrace_window`: the blk tracer records only from 2 s to 8 s after fio starts).

## Run

`.github/workflows/io-max-hidden-from-iostat.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. Each job is named after the section of the article it supplies and runs the conditions of that section with `./run-all.sh <section>`.

| Job | What it runs |
|---|---|
| `s3` | Section 3: no limit, `riops=1000` (three runs), batched reads with and without the limit, and two new loop devices with `rotational` set to 0 and 1 before the limit is first written |
| `s4` | Section 4: the limit set with `systemd-run`, `docker run` and a file on dm-linear, the blk tracer and the blktrace command on the limited device, the blk tracer started and stopped while fio runs, and libaio |
| `s5` | Section 5: 1, 4 and 64 threads, and `riops` 250 and 4000 |
| `report` | Merges the results of the other jobs into `results/summary.md` |

`results/summary.md` goes to the job summary of each job, with one part per section and the job name in each part's heading. Each job uploads `results/` as the `io-max-hidden-from-iostat-<job>` artifact, and the `report` job writes the merged summary to its job summary, to a comment on the open pull request for the branch (the same comment is overwritten on every run), and to the `io-max-hidden-from-iostat-summary` artifact.

A job fails with a `RUN INVALID` line at the top of `results/summary.md` when fio, iostat, pidstat, blktrace or blkparse is still missing after `apt-get install`, or when a condition did not run as set up: fio exited with an error or completed no reads, the cgroup's `io.max` does not hold the `riops` value of the condition, there are no `iostat` rows or cgroup samples, blkparse or the blk tracer recorded no events, the blk tracer wrote no `throtl [R] new slice` line in a limited condition it records from before fio starts, `CONFIG_HZ` cannot be read, or a slice in those lines is not 100 ms. `scripts/summary.py` also writes a `RUN INVALID` line for each condition of the job's sections that has no `results/<section>/<name>/analysis.json`.

## Setup

`run-all.sh` runs as root (it re-runs itself with `sudo`). It installs fio, sysstat and blktrace from apt when one of their commands is missing, sets `kernel.task_delayacct=1`, enables the io controller in the root cgroup, and creates `/sys/fs/cgroup/iomax-test`, in which fio runs unless the limit is set with `systemd-run` or `docker run`. Each loop device is made with `losetup --direct-io=on` on a file of random data in `/dev/shm`. For the dm condition, a separate loop device carries dm-linear (`iomax-lin`) with ext4, mounted at `/mnt/iomax-p9`, and fio reads a 256 MiB file on it. The Docker condition builds `docker/Dockerfile` and runs fio from that image with the loop device passed through.

`scripts/run_cond.sh` runs one condition. fio reads for `RUNTIME` seconds (10 by default) with `--output-format=json+`, and the arguments are written to `results/<section>/<name>/fio_args.txt`. Steady conditions use `psync` threads that each issue one read at a time, or `libaio` with an `iodepth`. Batched conditions use `io_uring` with `iodepth`, `iodepth_batch_submit` and `thinktime_blocks` equal to the batch size, `thinktime_blocks_type=issue`, and a 250 ms `thinktime`. Once fio has started, `iostat -dxy 1`, `pidstat -d 1` and `scripts/sample_cg.py` (`io.stat` and `io.pressure` of the cgroup, once a second) take nine one-second samples.

When the blk tracer records a condition, `scripts/run_cond.sh` writes each command it runs to `ftrace_commands.txt` and the commands it uses to read the `throtl [R] bio.` lines, with their output, to `ftrace_extract.txt`. The blktrace command condition writes its commands to `blktrace_commands.txt`.

Once the cgroup of fio exists, `scripts/run_cond.sh` writes these commands, each after `$ ` and followed by its output: `lsblk -o NAME,KNAME,MAJ:MIN` for the limited device to `lsblk.txt`; `cat /proc/<PID>/cgroup` for the first PID in that cgroup's `cgroup.procs`, then `cat` of the `io.max` under the path it prints, to `proc_cgroup.txt`; and `systemctl show -p IOReadIOPSMax` for the unit or `docker inspect -f '{{json .HostConfig.BlkioDeviceReadIOps}}'` for the container to `limit_setting.txt`. In an `ftrace_window` condition, the line of the limited device in the `io.stat` of the cgroup of fio is read right after the tracer is on and again right before it is turned off, into `io.stat_trace.txt`, and the seconds after fio started at which the tracer was turned on and off and `io.stat` was read go to `window_times.txt`.

`scripts/analyze.py` writes `results/<section>/<name>/analysis.json`: fio latency percentiles, mean and the share of reads over 50 ms from the latency histogram, the predicted share (concurrent reads divided by `riops` / 10, or for batches the reads beyond `riops` / 10 divided by the batch size), the `iostat` means, the `io.stat` reads per second, the `io.pressure` `some` time, the summed `iodelay`, the slice length from `new slice start= end=` divided by `CONFIG_HZ`, and for batched conditions the time from the first to the last read of each batch. A read held by the limit is traced as `Q` only when it is released, so its submission time is taken from its `throtl [R] bio.` line.

The rest of `results/`:

- `host/<targets>/environment.txt`: kernel, `CONFIG_HZ`, tool versions and cgroup controllers
- `host/<targets>/devices.tsv`: each device's number, `rotational` when it was made, and whether direct I/O is on
- `host/<targets>/timing.tsv`: seconds per condition and per section
- `<section>/<name>/settings.txt`: the condition, `rotational` of each device read, `CONFIG_HZ`, and `rotational` when `io.max` was first written to the device

## Pinned versions

fio comes from the Ubuntu 24.04 packages, on the runner and in `docker/Dockerfile` (`ubuntu:24.04`). The runner image is `ubuntu-24.04`.

- `io.max` is checked before a read enters the device queue, and `io.stat` counts reads after that check: https://github.com/torvalds/linux/blob/v6.17/block/blk-core.c#L874 , https://github.com/torvalds/linux/blob/v6.17/block/blk-core.c#L730-L736
- The slice is `HZ / 10` for every device from v6.17.13 (v6.17.12 still chooses `HZ / 50` for non-rotational devices): https://github.com/gregkh/linux/blob/v6.17.13/block/blk-throttle.c#L25 , https://github.com/gregkh/linux/blob/v6.17.12/block/blk-throttle.c#L25-L26 , https://github.com/torvalds/linux/commit/f76581f9f1d2
- The `throtl [R] bio.` and `new slice` lines, written only while block tracing is on: https://github.com/torvalds/linux/blob/v6.17/block/blk-throttle.c#L1813-L1814 , https://github.com/torvalds/linux/blob/v6.17/block/blk-throttle.c#L536-L542 , https://github.com/torvalds/linux/blob/v6.17/block/blk-throttle.c#L120-L133
- fio `thinktime_blocks_type`: https://github.com/axboe/fio/blob/fio-3.36/HOWTO.rst#L3208-L3237
