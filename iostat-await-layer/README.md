# iostat-await-layer

Feasibility check. Does a wait created by a cgroup v2 `io.max` limit (blk-throttle, above the device) show up in `iostat -x` `r_await`, compared with a wait created on the device side (null_blk `completion_nsec` / `mbps`, dm-delay)?

- `probe.sh`: kernel, cgroup mounts, null_blk and dm-delay availability, `io.max` write test. Log: `results/probe.log`.
- `measure.sh`: fio 4 KiB O_DIRECT random read at iodepth 1 and 16 under each condition, with `iostat -dxy` over 8 s of the 10 s run. fio runs inside `/sys/fs/cgroup/iotest`.
- `scripts/summary.py`: table of fio clat/lat p50/p99 and iostat r/s, r_await, aqu-sz.

Run on Linux as root: `sudo ./run-all.sh`. CI: `.github/workflows/iostat-await-layer.yml`.
