#!/usr/bin/env python3
import contextlib
import io
import os
import statistics
import sys

here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results = sys.argv[1]
MIB = 1048576


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def conditions():
    rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(here, "conditions.tsv"))]
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def load(cond, run):
    d = os.path.join(results, cond, run)
    r = {"cond": cond, "run": run, "dir": d, "cycles": {}, "idle": None, "injected": None, "call": None}
    for line in read(os.path.join(d, "events.txt")).splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "peak":
            c = r["cycles"].setdefault(int(p[2]), {})
            c.update(peak_t=float(p[1]), peak_s=float(p[3]), first=int(p[4]), last=int(p[5]), k=int(p[6]), minflt=int(p[7]))
        elif p[0] == "mi" and p[3] in ("hold", "wait"):
            c = r["cycles"].setdefault(int(p[2]), {})
            c[p[3]] = dict(t=float(p[1]), arena=int(p[4]), uord=int(p[5]), ford=int(p[6]), hblkhd=int(p[7]))
        elif p[0] == "rel":
            c = r["cycles"].setdefault(int(p[2]), {})
            c.update(rel_t=float(p[1]), rss_before_gc=int(p[3]), rss_after_gc=int(p[4]))
        elif p[0] == "trim":
            r["cycles"].setdefault(int(p[2]), {}).update(trim_s=float(p[3]))
        elif p[0] == "idle":
            r["idle"] = float(p[1])
        elif p[0] == "injected":
            r["injected"] = dict(t=float(p[1]), trim_s=float(p[2]), ret=int(p[3]))
    for line in read(os.path.join(d, "inject.txt")).splitlines():
        p = line.split()
        r["call"] = dict(t=float(p[1]), returned=float(p[2]), result=" ".join(p[3:]))
    r["samples"] = []
    for line in read(os.path.join(d, "samples.tsv")).splitlines()[1:]:
        p = line.split("\t")
        r["samples"].append(dict(t=float(p[0]), rss=int(p[1]),
                                 hsize=int(p[2]) if p[2] != "-" else None,
                                 hrss=int(p[3]) if p[3] != "-" else None,
                                 hend=int(p[4], 16) if p[4] != "-" else None))
    r["env"] = dict(l.split(" ", 1) for l in read(os.path.join(d, "env.txt")).splitlines() if " " in l)
    st = dict(l.split("=", 1) for l in read(os.path.join(d, "settings.txt")).splitlines() if "=" in l)
    r["args"] = st.get("observer_args", "").split()
    r["container"] = dict(l.split(" ", 1) for l in read(os.path.join(d, "container.txt")).splitlines() if " " in l)
    return r


def at(r, t):
    s = [x for x in r["samples"] if x["t"] <= t]
    return s[-1] if s else None


def job(r):
    return "%s %s" % (r["cond"], r["run"])


def cyc(r, n):
    return r["cycles"].get(n, {})


def point(r, n, phase):
    c = cyc(r, n)
    if phase not in c:
        return None, {}
    return at(r, c[phase]["t"]), c[phase]


def table(heads, rows):
    print("| " + " | ".join(heads) + " |")
    print("|" + "---|" * len(heads))
    for row in rows:
        print("| " + " | ".join(str(x) for x in row) + " |")
    print()


def med(v):
    return statistics.median(v) if v else None


def cnt(v):
    return "%d" % v if v == int(v) else "%.1f" % v


def ms(v):
    return "-" if v is None else "%.1f" % (v * 1000)


spreads = []


def mid(rs, f, scale, label):
    v = [x for x in (f(r) for r in rs) if x is not None]
    if not v:
        return "-"
    spreads.append(((max(v) - min(v)) / scale, label))
    return "%.1f" % (statistics.median(v) / scale)


def kib(rs, f, label):
    return mid(rs, f, 1024, label)


def byt(rs, f, label):
    return mid(rs, f, MIB, label)


def rss_at(n, ph):
    return lambda r: (point(r, n, ph)[0] or {}).get("rss")


def rss_return(r):
    call = r["call"]
    if not call:
        return None
    before = at(r, call["t"])
    after = [x for x in r["samples"] if x["t"] > call["t"]]
    if not before or not after:
        return None
    return before, after[-1]


def keep_place(r):
    out = []
    c1, s1 = cyc(r, 1), point(r, 1, "hold")[0]
    if c1 and s1 and s1["hend"] and s1["hsize"]:
        lo = s1["hend"] - s1["hsize"] * 1024
        out.append("outside [heap]" if not lo <= c1["k"] < s1["hend"] else "inside [heap]")
    else:
        out.append("-")
    c2, s2 = cyc(r, 2), point(r, 2, "hold")[0]
    if c2 and s2 and s2["hend"] and c2["last"] < c2["k"] < s2["hend"]:
        out.append("right after the last peak object (+%d), %d KiB before end of [heap]" % (c2["k"] - c2["last"], (s2["hend"] - c2["k"]) // 1024))
    else:
        out.append("not between the last peak object and end of [heap]")
    places = []
    for n in range(3, 17):
        c, p = cyc(r, n), cyc(r, n - 1)
        if not c or not p:
            continue
        if c["k"] > p["k"] and c["k"] > c["last"]:
            places.append("right after the previous keep object (+%d)" % (c["k"] - p["k"]))
        elif c["first"] <= c["k"] <= c["last"]:
            places.append("inside the peak range")
        else:
            places.append("below the peak range")
    out.append("; ".join("%s x%d" % (u, places.count(u)) for u in sorted(set(places))) or "-")
    c16, s16 = cyc(r, 16), point(r, 16, "hold")[0]
    if c16 and s16 and s16["hend"] and c16["last"] < c16["k"] < s16["hend"]:
        out.append("%d KiB before end of [heap]" % ((s16["hend"] - c16["k"]) // 1024))
    else:
        out.append("not between the last peak object and end of [heap]")
    return out


def main():
    conds = conditions()
    runs = {}
    for c in conds:
        d = os.path.join(results, c["name"])
        if os.path.isdir(d):
            runs[c["name"]] = [load(c["name"], x) for x in sorted(os.listdir(d)) if x.startswith("run")]
    status = 0
    problems = []
    cfg = {c["name"]: c for c in conds}
    for name, rs in runs.items():
        cycles = int(os.environ.get("CYCLES", "16"))
        for r in rs:
            if r["container"].get("exit") != "0":
                problems.append("%s: container exit %s" % (job(r), r["container"].get("exit", "-")))
            if any("wait" not in cyc(r, n) for n in range(1, cycles + 1)) or r["idle"] is None:
                problems.append("%s: not all %d cycles completed" % (job(r), cycles))
            if cfg[name]["inject_malloc_trim"] == "1" and not (r["call"] and r["call"]["result"] == "ok" and r["injected"]):
                problems.append("%s: sys.remote_exec did not run the script" % job(r))
            wait_s = float(os.environ.get("WAIT_S", "12"))
            slow = [n for n in range(1, cycles + 1) if "wait" in cyc(r, n) and "rel_t" in cyc(r, n) and cyc(r, n)["wait"]["t"] - cyc(r, n)["rel_t"] > wait_s + 2]
            if slow:
                problems.append("%s: the wait took more than %.0f s in cycles %s" % (job(r), wait_s + 2, ", ".join(map(str, slow))))
            if name.startswith(("p1", "p5")):
                c2 = cyc(r, 2)
                hold = at(r, c2["hold"]["t"]) if "hold" in c2 else None
                if not c2 or not hold or not (c2["k"] > c2["last"] and hold["hend"] and c2["k"] < hold["hend"]):
                    problems.append("%s: cycle 2 keep object is not between the last peak object and the end of [heap]" % job(r))
    if problems:
        status = 1

    p1 = runs.get("p1-default", [])
    body = io.StringIO()
    with contextlib.redirect_stdout(body):
        print("## 3. Reproduce and check")
        print()
        scopes = sorted({r["env"].get("ptrace_scope", "-") for rs in runs.values() for r in rs})
        print("The workload was started as a child process of the observer process, and sys.remote_exec was called from that parent; ptrace_scope was %s and the container had Docker's default capabilities (no --cap-add)." % " / ".join(scopes))
        print()
        if p1:
            print("### p1-default")
            print()
            rows = []
            for n, ph in ((1, "hold"), (1, "wait"), (2, "hold"), (2, "wait"), (16, "wait")):
                lab = lambda f: "p1-default, %s at end of %s, cycle %d" % (f, ph, n)
                mi = lambda key: (lambda r: point(r, n, ph)[1].get(key))
                rows.append(["cycle %d, end of %s" % (n, ph), kib(p1, rss_at(n, ph), lab("VmRSS")),
                             byt(p1, mi("hblkhd"), lab("hblkhd")),
                             byt(p1, mi("ford"), lab("fordblks")),
                             byt(p1, mi("uord"), lab("uordblks"))])
            table(["moment", "VmRSS", "hblkhd", "fordblks", "uordblks"], rows)
            before = kib(p1, lambda r: cyc(r, 2).get("rss_before_gc"), "p1-default, VmRSS before gc.collect(), cycle 2")
            after = kib(p1, lambda r: cyc(r, 2).get("rss_after_gc"), "p1-default, VmRSS after gc.collect(), cycle 2")
            print("p1-default, cycle 2: VmRSS read by the process itself from /proc/self/status after the list was dropped was %s MiB just before gc.collect() and %s MiB just after." % (before, after))
            print()
        print("### By condition")
        print()
        rows = []
        for name in ("p1-default", "p2-no-keep", "p3-mmap-threshold", "p4-trim-threshold", "p5-trim-after-release"):
            rs = runs.get(name)
            if rs:
                rows.append([name, kib(rs, rss_at(2, "wait"), "%s, VmRSS at end of wait, cycle 2" % name),
                             kib(rs, rss_at(16, "wait"), "%s, VmRSS at end of wait, cycle 16" % name)])
        table(["condition", "VmRSS at end of wait, cycle 2", "VmRSS at end of wait, cycle 16"], rows)

        if p1:
            print("## 4. Check in your own process")
            print()
            print("p1-default, after cycle 16: malloc_trim(0) run in the process with sys.remote_exec(pid, script).")
            print()
            rr = lambda i, key: (lambda r: (rss_return(r) or ({}, {}))[i].get(key))
            row = ["p1-default"]
            for key, lab in (("rss", "VmRSS"), ("hsize", "[heap] Size"), ("hrss", "[heap] Rss")):
                row += [kib(p1, rr(0, key), "p1-default %s before malloc_trim(0)" % lab),
                        kib(p1, rr(1, key), "p1-default %s after malloc_trim(0)" % lab)]
            table(["condition", "VmRSS before", "VmRSS after", "[heap] Size before", "[heap] Size after",
                   "[heap] Rss before", "[heap] Rss after"], [row])
            print("Before: last sample before the sys.remote_exec() call. After: last sample of the run. Samples are 0.5 s apart.")
            print()

        print("## 5. Mitigations")
        print()
        print("From cycle 3 on, how much memory the peak stage had to obtain from the kernel again because it had been returned (a minor page fault: the kernel zero-fills a page and maps it at the first write).")
        print()
        rows = []
        for name in ("p1-default", "p3-mmap-threshold", "p4-trim-threshold", "p5-trim-after-release"):
            rs = runs.get(name)
            if not rs:
                continue
            flt = lambda r: med([cyc(r, n)["minflt"] for n in range(3, 17) if "minflt" in cyc(r, n)])
            v = [x for x in (flt(r) for r in rs) if x is not None]
            pages = lambda r: flt(r) * int(r["env"]["pagesize"]) if flt(r) is not None and r["env"].get("pagesize", "").isdigit() else None
            rows.append([name, cnt(med(v)) if v else "-", byt(rs, pages, "%s, pages obtained from the kernel again" % name)])
        table(["condition", "minor page faults per peak stage, median of cycles 3-16", "pages obtained from the kernel again (MiB)"], rows)
        p5 = runs.get("p5-trim-after-release", [])
        if p5:
            t = [x for x in (med([cyc(r, n)["trim_s"] for n in range(1, 17) if "trim_s" in cyc(r, n)]) for r in p5) if x is not None]
            print("p5-trim-after-release: malloc_trim(0) after each release took %s ms (median over cycles 1 to 16)." % ms(med(t)))
            print()

        print("## Numbers not used")
        print()
        if p1:
            places = [keep_place(r) for r in p1]
            heads = ["cycle 1", "cycle 2", "cycles 3-16", "cycle 16"]
            if all(x == places[0] for x in places):
                print("- Keep object (p1-default, same in all %d runs): " % len(p1) + "; ".join("%s %s" % (h, v) for h, v in zip(heads, places[0])))
            else:
                print("- Keep object (p1-default): " + " | ".join("%s: " % r["run"] + "; ".join("%s %s" % (h, v) for h, v in zip(heads, pl)) for r, pl in zip(p1, places)))
        envs = {}
        for rs in runs.values():
            for r in rs:
                e = r["env"]
                key = "%s, %s, Python %s, GIL enabled %s" % (e.get("glibc", "-"), e.get("uname_m", "-"), e.get("python", "-"), e.get("gil_enabled", "-"))
                envs.setdefault(key, []).append(job(r))
        if len(envs) == 1:
            print("- Environment (all runs): " + list(envs)[0])
        else:
            print("- Environment: " + " | ".join("%s: %s" % (", ".join(j), k) for k, j in envs.items()))
        print()

    counts = sorted({len(rs) for rs in runs.values()})
    top = max(x[0] for x in spreads) if spreads else 0
    over = [x for x in spreads if x[0] > 0.5]
    line = "%s runs per condition, median shown. The largest spread between runs among the MiB values shown is %.2f MiB" % ("/".join(map(str, counts)), top)
    line += "; values with a spread over 0.5 MiB: %s." % ", ".join("%s (%.2f MiB)" % (l, v) for v, l in over) if over else "; no value shown has a spread over 0.5 MiB."
    shapes = []
    for rs in runs.values():
        for r in rs:
            a = r["args"]
            if len(a) >= 10:
                x = "1 cycle = create %s objects of %s MiB, hold %s s, drop them and call gc.collect(), wait %s s; %s cycles per run." % (a[6], "%g" % (int(a[7]) / MIB), a[8], a[9], a[5])
                if x not in shapes:
                    shapes.append(x)
    print("# glibc-heap-not-returned")
    print()
    print(" ".join(shapes) + " End of hold: after the hold, just before the objects are dropped. End of wait: after the wait, just before the next cycle. VmRSS, [heap] Size and Rss: MiB, from KiB in /proc/<pid>/status and /proc/<pid>/smaps (sampled every 0.5 s from outside the process; the value shown is the last sample before the moment). mallinfo2 fields: MiB, from bytes, called inside the process at that moment. malloc_trim(0) duration: ms. Minor page faults: count. " + line)
    print()
    if problems:
        print("## Invalid runs")
        print()
        for p in problems:
            print("- " + p)
        print()
    sys.stdout.write(body.getvalue())
    return status


sys.exit(main())
