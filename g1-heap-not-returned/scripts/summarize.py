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
    flag_line = re.search(r"^.*\bG1PeriodicGCInterval\b.*=.*$", stdout, re.M)
    r["periodic_interval_flag_line"] = flag_line.group(0).strip() if flag_line else None
    err = re.search(r"^(?:Unrecognized VM option|Error: ).*$", stdout, re.M)
    r["startup_error_line"] = err.group(0).strip() if err else None
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
    using_full = re.search(r"^\[[^\]]*\]\[[^\]]*\]\[gc *\] (Using \w+)", log, re.M)
    r["using_line_text"] = using_full.group(0) if using_full else None
    first_mx = next((l for l in stdout.splitlines() if l.startswith("mx ")), None)
    r["pools"] = re.findall(r'"([^"]+)"=', first_mx) if first_mx else []
    mxs = []
    for l in stdout.splitlines():
        if l.startswith("mx "):
            m = re.search(r"heap_committed_mb=([\d.]+)", l)
            mxs.append((float(l.split()[1]), float(m.group(1))))
    if after is not None:
        mx_peak = [x for x in mxs if x[0] < after]
        r["mx_committed_mb_end_of_peak"] = mx_peak[-1][1] if mx_peak else None
        r["mx_committed_mb_last"] = mxs[-1][1] if mxs else None
        young_after = [x for x in g if x[0] >= after and x[1] == "Young" and re.search(r"\]\[gc *\] ", x[4])]
        r["young_gc_line_after_peak"] = young_after[0][4] if young_after else None
        skip = [l for l in log.splitlines() if "Skipping" in l and UL_TS.match(l) and float(UL_TS.match(l).group(1)) >= after]
        r["skipping_line_after_peak"] = skip[0] if skip else None
        pgc = [x for x in g if x[1] == "Young" and "(G1 Periodic Collection)" in x[4] and re.search(r"\]\[gc *\] ", x[4])]
        if pgc:
            tp = pgc[0][0]
            r["periodic_gc_line"] = pgc[0][4]
            r["periodic_first_s_after_peak"] = round(tp - after, 1)
            before = [x for x in mxs if x[0] < tp]
            aft = [x for x in mxs if x[0] > tp]
            r["periodic_mx_committed_mb_before"] = before[-1][1] if before else None
            r["periodic_mx_committed_mb_after"] = aft[0][1] if aft else None
            remark = [x for x in g if x[0] >= tp and x[1] == "Remark" and re.search(r"\]\[gc *\] ", x[4])]
            if remark:
                r["periodic_remark_line"] = remark[0][4]
                gid = re.search(r"GC\((\d+)\)", remark[0][4]).group(1)
                sh = [l for l in log.splitlines() if "GC(%s) Shrink the heap" % gid in l]
                r["periodic_remark_shrink_line"] = sh[0] if sh else None
    return r


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return "%.1f" % v
    if isinstance(v, (list, tuple)):
        return ", ".join(fmt(x) for x in v) or "-"
    return str(v)


def sections():
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "conditions.tsv")) as f:
        rows = [l.rstrip("\n").split("\t") for l in f][1:]
    return {r[0]: r[6] for r in rows}


SECTION = sections()


def job_name(r):
    return "%s (%s)" % (SECTION.get(r["cond"], "-"), r["cond"])


def job(r):
    return "%s %s" % (job_name(r), r["runname"])


def table(heads, rows):
    if not rows:
        return
    print("| job | run | " + " | ".join(heads) + " |")
    print("|---|---|" + "---|" * len(heads))
    for r, cells in rows:
        print("| %s | %s | " % (job_name(r), r["runname"]) + " | ".join(fmt(c) for c in cells) + " |")
    print()


def lines(rows, key):
    out = [(r, r.get(key)) for r in rows]
    for r, l in out:
        print("- %s: `%s`" % (job(r), l if l else "-"))
    if out:
        print()


def section(title, rows):
    if rows:
        print("## %s\n" % title)
    return bool(rows)


def pick(rows, conds):
    return [r for c in conds for r in rows if r["cond"] == c]


def diff(a, b):
    return b - a if a is not None and b is not None else None


def committed(r):
    return [r.get("mx_committed_mb_end_of_peak"), r.get("mx_committed_mb_last")]


COMMITTED = ["heap committed MB, end of peak", "heap committed MB, last"]


def md(root, conds):
    rows = []
    bad = []
    for c in conds:
        cdir = os.path.join(root, c)
        for run in sorted(os.listdir(cdir)) if os.path.isdir(cdir) else []:
            if not os.path.isdir(os.path.join(cdir, run)):
                continue
            r = main(os.path.join(cdir, run))
            r["cond"], r["runname"] = c, run
            rows.append(r)
            with open(os.path.join(cdir, run, "summary.json"), "w") as f:
                json.dump(r, f, indent=1)
            if c == "jdk11-periodic":
                if not r["startup_error_line"] or r["java_exit"] in (None, "0"):
                    bad.append("%s/%s: the JVM did not refuse the option" % (c, run))
                continue
            if not r["gc_flag"]:
                bad.append("%s/%s: no GC flag in the output" % (c, run))
            if c == "jdk25-flags" and not r["periodic_interval_flag_line"]:
                bad.append("%s/%s: no G1PeriodicGCInterval in the flags" % (c, run))
            if r.get("gc_count_after") is not None and not r["phase_end"]:
                bad.append("%s/%s: the program did not finish" % (c, run))
    print("# g1-heap-not-returned\n")
    print("Heap committed is the sum of the committed sizes of the heap memory pools (MemoryPoolMXBean).\n")
    s = pick(rows, ["jdk8", "jdk11", "jdk25"])
    if section("1. Opening", s):
        table(["memory.current change MB, end of peak to last"],
              [(r, [diff(r.get("memory_current_mb_end_of_peak"), r.get("memory_current_mb_last"))]) for r in s])
    s1 = pick(rows, ["jdk8"])
    s2 = pick(rows, ["jdk11", "jdk25"])
    s3 = pick(rows, ["jdk8", "jdk11"])
    s4 = pick(rows, ["jdk25-periodic-idle"])
    s5 = pick(rows, ["jdk25-flags"])
    s6 = pick(rows, ["jdk11-periodic"])
    s7 = pick(rows, ["jdk25-memory1g", "jdk25-cpus1"])
    if section("2. What changed", s1 + s2 + s3 + s4 + s5 + s6 + s7):
        lines(s1, "gc_flag")
        lines(s2, "using_line_text")
        table(COMMITTED, [(r, committed(r)) for r in s3])
        lines(s4, "periodic_remark_line")
        lines(s4, "periodic_remark_shrink_line")
        lines(s5, "periodic_interval_flag_line")
        lines(s6, "startup_error_line")
        lines(s7, "using_line_text")
    s1 = pick(rows, ["jdk25-periodic"])
    s2 = pick(rows, ["jdk25-periodic-idle"])
    if section("3. Periodic GC", s1 + s2):
        lines(s1, "skipping_line_after_peak")
        lines(s2, "periodic_gc_line")
        table(["heap committed MB, before", "heap committed MB, after"],
              [(r, [r.get("periodic_mx_committed_mb_before"), r.get("periodic_mx_committed_mb_after")]) for r in s2])
    s1 = pick(rows, ["jdk11"])
    s2 = pick(rows, ["jdk8", "jdk11", "jdk17", "jdk21", "jdk25"])
    if section("4. Telling it apart in your environment", s1 + s2):
        lines(s1, "young_gc_line_after_peak")
        table(["heap memory pools"], [(r, [r.get("pools")]) for r in s2])
    s = pick(rows, ["jdk11-parallel"])
    if section("5. Countermeasure", s):
        table(COMMITTED, [(r, committed(r)) for r in s])
    s1 = pick(rows, ["jdk17", "jdk21", "jdk25"])
    s2 = pick(rows, ["jdk11", "jdk17", "jdk21", "jdk25", "jdk25-periodic", "jdk25-periodic-idle"])
    s3 = pick(rows, ["jdk8"])
    s4 = pick(rows, ["jdk25-periodic-idle"])
    if section("Numbers not used", s1 + s2 + s3 + s4):
        table(COMMITTED, [(r, committed(r)) for r in s1])
        table(["concurrent starts after peak"], [(r, [r.get("concurrent_start_after_peak")]) for r in s2])
        table(["reduced footprint lines after peak"], [(r, [r.get("reduced_footprint_after")]) for r in s3])
        table(["periodic GC, s after end of peak"], [(r, [r.get("periodic_first_s_after_peak")]) for r in s4])
    if bad:
        print("## Invalid runs\n")
        for b in bad:
            print("- " + b)
        return 1
    return 0


if sys.argv[1] == "--md":
    sys.exit(md(sys.argv[2], sys.argv[3:]))
for d in sys.argv[1:]:
    print(json.dumps(main(d)))
