#!/usr/bin/env python3
import json
import os
import re
import sys

J8_TS = re.compile(r"^(\d+\.\d+): \[(Full GC|GC)")
J8_GC = re.compile(r"\[PSYoungGen: (\d+)K->(\d+)K\((\d+)K\)\] (\d+)K->(\d+)K\((\d+)K\), ([\d.]+) secs\]")
J8_FULL = re.compile(r"\] (\d+)K->(\d+)K\((\d+)K\), \[Metaspace: [^\]]*\], ([\d.]+) secs\]")
UL_GC = re.compile(r"^\[([\d.]+)s\].*GC\(\d+\) Pause (Young|Remark|Cleanup|Full)\b(.*?) (\d+)M->(\d+)M\((\d+)M\) ([\d.]+)ms")
UL_TS = re.compile(r"^\[([\d.]+)s\]")


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def gcs(d):
    log = read(os.path.join(d, "gc.log"))
    out = []
    ts = None
    for line in log.splitlines():
        m = J8_TS.match(line)
        if m:
            ts = float(m.group(1))
            kind = "Full" if m.group(2) == "Full GC" else "Young"
        f = J8_FULL.search(line) if "[ParOldGen" in line else None
        if f and ts is not None:
            out.append((ts, "Full", int(f.group(3)) / 1024, float(f.group(4)) * 1000, line))
            continue
        m = J8_GC.search(line)
        if m and ts is not None:
            out.append((ts, kind, int(m.group(6)) / 1024, float(m.group(7)) * 1000, line))
            continue
        m = UL_GC.match(line)
        if m:
            out.append((float(m.group(1)), m.group(2), float(m.group(6)), float(m.group(7)), line))
    return log, out


def main(d):
    stdout = read(os.path.join(d, "stdout.txt"))
    log, g = gcs(d)
    phases = {m.group(1): float(m.group(2)) for m in re.finditer(r"^phase (\w+) ([\d.]+)", stdout, re.M)}
    peak, after, end = phases.get("peak", 0), phases.get("after"), phases.get("end")
    r = {"run": d}
    flags = re.search(r"-XX:\+Use(\w+)GC\b", stdout)
    r["gc_flag"] = flags.group(0) if flags else None
    using = re.search(r"\] Using (\w+)", log)
    r["using_line"] = using.group(1) if using else None
    r["java_exit"] = (re.search(r"java exit (\d+)", stdout) or [None, None])[1]
    if after is not None:
        in_peak = [x for x in g if x[0] < after]
        in_after = [x for x in g if x[0] >= after]
        r["heap_committed_mb_end_of_peak"] = in_peak[-1][2] if in_peak else None
        r["heap_committed_mb_max"] = max(x[2] for x in g) if g else None
        r["heap_committed_mb_last_gc"] = g[-1][2] if g else None
        r["gc_count_peak"] = len(in_peak)
        r["gc_count_after"] = len(in_after)
        stop = end if end is not None else (in_after[-1][0] if in_after else after)
        if stop > after:
            r["gc_time_pct_after"] = round(100 * sum(x[3] for x in in_after) / ((stop - after) * 1000), 3)
        r["full_gc_count"] = sum(1 for x in g if x[1] == "Full")
        conc = [x[0] for x in g if x[1] == "Young" and re.search(r"\]\[gc *\] ", x[4]) and "(Concurrent Start)" in x[4]]
        r["concurrent_start_count"] = len(conc)
        r["concurrent_start_after_peak"] = sum(1 for t in conc if t >= after)
        r["remark_count"] = sum(1 for x in g if x[1] == "Remark")
        exp = [float(m.group(1)) for m in re.finditer(r"^\[([\d.]+)s\].*(?:Expand the heap|Attempt heap expansion \(recent GC overhead|Heap expansion: .*resize by [1-9])", log, re.M)]
        r["expansion_lines_peak"] = sum(1 for t in exp if t < after)
        r["expansion_lines_after"] = sum(1 for t in exp if t >= after)
        shrink = [float(m.group(1)) for m in re.finditer(r"^\[([\d.]+)s\].*(?:Shrink the heap|Uncommit|shrink)", log, re.M | re.I)]
        r["shrink_lines_after"] = sum(1 for t in shrink if t >= after)
        rf = 0
        last_ts = None
        for line in log.splitlines():
            m = J8_TS.match(line)
            if m:
                last_ts = float(m.group(1))
            if "reduced footprint" in line and last_ts is not None and last_ts >= after:
                rf += 1
        r["reduced_footprint_after"] = rf
        r["periodic_skipping"] = len(re.findall(r"Last GC occurred .* Skipping", log))
        r["periodic_gc"] = sum(1 for x in g if x[1] == "Young" and re.search(r"\]\[gc *\] ", x[4]) and "(G1 Periodic Collection)" in x[4])
        mx = [l for l in stdout.splitlines() if l.startswith("mx ")]
        def eden(l):
            m = re.search(r'"PS Eden Space"=([\d.]+)', l)
            return float(m.group(1)) if m else None
        mx_peak = [l for l in mx if float(l.split()[1]) < after]
        if mx_peak and eden(mx_peak[-1]) is not None:
            r["ps_eden_committed_mb_end_of_peak"] = eden(mx_peak[-1])
            r["ps_eden_committed_mb_max"] = max(eden(l) for l in mx)
            r["ps_eden_committed_mb_last"] = eden(mx[-1])
    js = read(os.path.join(d, "java-start.txt")).strip()
    settings = read(os.path.join(d, "t0.txt")).strip()
    if js and settings:
        off = float(js) - float(settings)
        rows = [l.split("\t") for l in read(os.path.join(d, "memory.tsv")).splitlines()[1:]]
        rows = [(float(a[0]) - off, float(a[1])) for a in rows]
        if after is not None and rows:
            before_after = [x for x in rows if x[0] < after]
            r["memory_current_mb_end_of_peak"] = before_after[-1][1] if before_after else None
            r["memory_current_mb_max"] = max(x[1] for x in rows)
            alive = [x for x in rows if end is None or x[0] <= end]
            r["memory_current_mb_last"] = alive[-1][1] if alive else None
    jv = re.search(r'version "([^"]+)"', read(os.path.join(d, "java-version.txt")))
    r["java_version"] = jv.group(1) if jv else None
    r["phase_end"] = end is not None
    mx = [l for l in stdout.splitlines() if l.startswith("mx ")]
    rates = {}
    for l in mx[1:]:
        ph = l.split()[2]
        m = re.search(r"alloc_mb=([\d.]+)", l)
        rates.setdefault(ph, []).append(float(m.group(1)))
    r["alloc_mb_per_s_median"] = {k: sorted(v)[len(v) // 2] for k, v in rates.items() if len(v) > 2}
    cg = read(os.path.join(d, "cgroup.txt"))
    r["cgroup"] = dict(l.split(" ", 1) for l in cg.splitlines() if " " in l)
    return r


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return "%.1f" % v
    if isinstance(v, list):
        return "<br>".join(v) or "-"
    return str(v)


COLS = [
    ("Java", lambda r: r.get("java_version")),
    ("GC", lambda r: (r["gc_flag"] or "").replace("-XX:+Use", "").replace("GC", "") or "-"),
    ("heap committed MB, end of peak", lambda r: r.get("heap_committed_mb_end_of_peak")),
    ("heap committed MB, last GC", lambda r: r.get("heap_committed_mb_last_gc")),
    ("PS Eden committed MB, end of peak", lambda r: r.get("ps_eden_committed_mb_end_of_peak")),
    ("PS Eden committed MB, last", lambda r: r.get("ps_eden_committed_mb_last")),
    ("memory.current MB, end of peak", lambda r: r.get("memory_current_mb_end_of_peak")),
    ("memory.current MB, last", lambda r: r.get("memory_current_mb_last")),
    ("GC time % after peak", lambda r: r.get("gc_time_pct_after")),
    ("concurrent starts (after peak)", lambda r: "%s (%s)" % (r.get("concurrent_start_count"), r.get("concurrent_start_after_peak"))),
    ("Skipping lines / periodic GCs", lambda r: "%s / %s" % (r.get("periodic_skipping"), r.get("periodic_gc"))),
]


def md(root, conds):
    rows = []
    bad = []
    for c in conds:
        cdir = os.path.join(root, c)
        for run in sorted(os.listdir(cdir)) if os.path.isdir(cdir) else []:
            r = main(os.path.join(cdir, run))
            r["cond"], r["runname"] = c, run
            rows.append(r)
            with open(os.path.join(cdir, run, "summary.json"), "w") as f:
                json.dump(r, f, indent=1)
            if not r["gc_flag"]:
                bad.append("%s/%s: no GC flag in the output" % (c, run))
            if r.get("gc_count_after") is not None and not r["phase_end"]:
                bad.append("%s/%s: the program did not finish" % (c, run))
    print("# g1-heap-not-returned\n")
    print("| condition | run | " + " | ".join(h for h, _ in COLS) + " |")
    print("|---|---|" + "---|" * len(COLS))
    for r in rows:
        print("| %s | %s | " % (r["cond"], r["runname"]) + " | ".join(fmt(f(r)) for _, f in COLS) + " |")
    print("\nJava versions: " + ", ".join(sorted({r["java_version"] or "-" for r in rows})))
    if bad:
        print("\n## Invalid runs\n")
        for b in bad:
            print("- " + b)
        return 1
    return 0


if sys.argv[1] == "--md":
    sys.exit(md(sys.argv[2], sys.argv[3:]))
for d in sys.argv[1:]:
    print(json.dumps(main(d)))
