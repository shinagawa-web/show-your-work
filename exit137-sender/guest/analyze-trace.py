#!/usr/bin/env python3
import json, os, re, sys
from datetime import datetime, timezone

root = sys.argv[1]
trace = open(os.path.join(root, "trace.txt")).read().splitlines()


def t(line):
    h, m, s = line.split()[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def tod(ns):
    d = datetime.fromtimestamp(ns / 1e9, timezone.utc)
    return d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6


rows = []
for cond in sorted((c for c in os.listdir(root) if re.fullmatch(r"C\d+", c)), key=lambda c: int(c[1:])):
    for run in sorted(os.listdir(os.path.join(root, cond)), key=int):
        d = os.path.join(root, cond, run)
        cid = json.load(open(os.path.join(d, "inspect-end.json")))[0]["Id"]
        pid1 = json.load(open(os.path.join(d, "inspect-start.json")))[0]["State"]["Pid"]
        ev = [json.loads(l) for l in open(os.path.join(d, "events.jsonl"))]
        has_oom = any(e["Action"] == "oom" for e in ev)
        scope = f"docker-{cid}.scope"
        kp = re.search(r"Killed process (\d+) ", open(os.path.join(d, "kernel.txt")).read())
        vpid = int(kp.group(1)) if kp else pid1
        victim = next((l for l in trace if "mark_victim" in l and f"pid={vpid} " in l), None)
        if not victim:
            rows.append((cond, run, has_oom, f"no mark_victim for pid {vpid}"))
            continue
        t0 = t(victim)
        pop0 = next((l for l in trace if "populated=0" in l and scope in l), None)
        rmd = next((l for l in trace if " rmdir " in l and scope in l), None)
        shim = next((re.search(r"pid=(\d+)", l).group(1) for l in trace if " open " in l and cid in l), None)
        opens = [l for l in trace if " open " in l and f"pid={shim} " in l and f"{scope}/" in l and t(l) >= t0]
        end = t(rmd) + 0.05 if rmd else t0 + 1
        notes = [l for l in trace if "kernfs_notify " in l and t0 - 0.01 <= t(l) <= end]
        work = [l for l in trace if "kernfs_notify_workfn" in l and t0 <= t(l) <= end]
        ireads = [l for l in trace if "inotify_read" in l and f"pid={shim} " in l and t0 <= t(l) <= end]
        faults = [l for l in trace if "filemap_fault" in l and f"pid={shim} " in l and t0 <= t(l) <= end]
        fsum = sum(int(re.search(r"took_us=(\d+)", l).group(1)) for l in faults)
        shimstat = f"slow filemap_fault {len(faults)} ({fsum / 1000:.1f} ms)"
        if os.path.exists(os.path.join(d, "shim.txt")):
            st = [l.split(": ")[1].split(" (")[0].split() for l in open(os.path.join(d, "shim.txt")) if ": " in l]
            if len(st) == 2 and len(st[1]) == 4:
                shimstat += f", shim majflt +{int(st[1][2]) - int(st[0][2])}"
        ms = lambda l: f"{(t(l) - t0) * 1000:+.1f}"
        rows.append((cond, run, has_oom,
                     "empty " + (ms(pop0) if pop0 else "-"),
                     "rmdir " + (ms(rmd) + " by " + re.search(r"comm=(\S+)", rmd).group(1) if rmd else "-"),
                     "shim opens " + (", ".join(f"{ms(l)} {l.split('/')[-1]} ret={re.search(r'ret=(-?\d+)', l).group(1)}" for l in opens[:2]) or "none"),
                     "kernfs_notify " + (", ".join(f"{ms(l)} {l.split()[2]}" for l in notes) or "none"),
                     "workfn " + (", ".join(ms(l) for l in work) or "none"),
                     "shim inotify reads " + (", ".join(f"{ms(l)} ret={re.search(r'ret=(-?\d+)', l).group(1)}" for l in ireads) or "none"),
                     shimstat))
for r in rows:
    print(" | ".join(str(x) for x in r))
