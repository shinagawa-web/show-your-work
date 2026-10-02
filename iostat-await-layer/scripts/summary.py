#!/usr/bin/env python3
"""Tabulate fio latency percentiles and iostat -x columns from results/.

Usage: summary.py <results dir>
Values are copied from fio JSON and iostat text without recomputation,
except ns -> ms for fio percentiles.
"""
import glob
import json
import os
import sys

d = sys.argv[1]
ORDER = ["a", "b", "c_compl", "c_mbps", "c_dm"]


def pct(job, kind, p):
    v = job["read"].get(kind, {}).get("percentile", {}).get(p)
    return "-" if v is None else f"{v / 1e6:.3f}"


def iostat_rows(path):
    rows = {}
    hdr = None
    for line in open(path):
        f = line.split()
        if not f:
            continue
        if f[0].startswith("Device"):
            hdr = f
            continue
        if hdr and len(f) == len(hdr):
            rows[f[0]] = dict(zip(hdr, f))
    return rows


tags = []
for p in glob.glob(os.path.join(d, "fio_*.json")):
    tags.append(os.path.basename(p)[4:-5])


def key(t):
    cond, qd = t.rsplit("_qd", 1)
    return (int(qd), ORDER.index(cond) if cond in ORDER else 99)


print("| cond | iodepth | fio IOPS | clat p50 ms | clat p99 ms | lat p50 ms | lat p99 ms "
      "| iostat dev | r/s | r_await ms | aqu-sz | %util |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for t in sorted(tags, key=key):
    cond, qd = t.rsplit("_qd", 1)
    job = json.load(open(os.path.join(d, f"fio_{t}.json")))["jobs"][0]
    iops = f"{job['read']['iops']:.0f}"
    lat = [pct(job, "clat_ns", "50.000000"), pct(job, "clat_ns", "99.000000"),
           pct(job, "lat_ns", "50.000000"), pct(job, "lat_ns", "99.000000")]
    rows = iostat_rows(os.path.join(d, f"iostat_{t}.txt"))
    first = True
    for dev, r in rows.items():
        lead = [cond, qd, iops] + lat if first else ["", "", ""] + [""] * 4
        print("| " + " | ".join(lead + [dev, r.get("r/s", "-"), r.get("r_await", "-"),
                                        r.get("aqu-sz", "-"), r.get("%util", "-")]) + " |")
        first = False
