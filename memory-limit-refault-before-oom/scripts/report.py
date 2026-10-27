import glob
import json
import math
import os
import sys

R = sys.argv[1]
S3 = os.path.join(R, "s3")


def base_name():
    for f in sorted(glob.glob(os.path.join(S3, "*", "sizes.txt"))):
        kv = dict(x.split("=", 1) for x in open(f).read().split() if "=" in x)
        if "target_headroom_base_mib" in kv:
            return f"{kv['target_headroom_base_mib']} MiB headroom"
    return "baseline"


BASE = base_name()
NAMES = {"n1": "page cache filled to the limit", "p3": "cache grows on a timer", "p3b": "cache grows per request", "f1": "limit raised"}


def load(job, run):
    p = os.path.join(S3, job, run, "summary.json")
    return json.load(open(p)) if os.path.exists(p) else None


def fmt(v, n=None):
    if v is None:
        return "-"
    if n is not None and isinstance(v, (int, float)):
        return f"{v:.{n}f}"
    return str(v)


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(fmt(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


lines = ["# memory-limit-refault-before-oom", ""]
stops = []
for job in ("p3", "p3b"):
    o = load(job, job)
    if not o:
        continue
    x = o["growth"]
    series = {r["t_s"]: r for r in x["series_1s"]}
    last = max(series)
    end_s = min(last, math.ceil(x["oom_kill_at_s"])) if x["oom_kill_at_s"] is not None else last
    picks = [0]
    if x["stop_s"] is not None:
        k = x["stop_s"]
        picks += [k - 5, k] + list(range(k + 10, end_s, 10))
    picks.append(end_s)
    picks = sorted({p for p in picks if 0 <= p <= last})
    rows = [[p, series[p]["completed"], series[p]["p99_ms"], series[p].get("anon_mib"), series[p].get("file_mib"), series[p].get("pgmajfault_per_s"), series[p].get("oom_kill")] for p in picks]
    how = f"{fmt(o['grow_kbps'])} KiB/s added by a timer task" if job == "p3" else f"One {o['elem_kb']} KiB element added every {o['grow_per_requests']} requests"
    if not stops:
        lines += ["## Section 3", ""]
    lines += [f"### {NAMES[job]}", "", f"Job `s3-{job}`. {how}, {fmt(o['rps'], 0)} requests/s, from {BASE}.", "",
              table(["t (s)", "completed (/s)", "p99 (ms)", "anon (MiB)", "file (MiB)", "pgmajfault (/s)", "oom_kill"], rows), ""]
    oom = fmt(x["oom_kill_at_s"]) if x["oom_kill_at_s"] is not None else f"none (cut off at {x['load_s']})"
    stops.append([NAMES[job], x["stop_s"], x["headroom_at_stop_mib"], x["file_mapped_at_stop_mib"], oom, x["mem_psi_some_median_while_stalled"]])
if stops:
    lines += ["### When it stopped", "", "Stopped: the first second with pgmajfault over 100/s.", "",
              table(["condition", "stopped at (s)", "headroom at stop (MiB)", "file_mapped at stop (MiB)", "OOM kill at (s)", "memory PSI some while stopped, median (s/s)"], stops), ""]

rows = []
for name, job, run in ((BASE, "n1", "base"), (NAMES["n1"], "n1", "n1")):
    o = load(job, run)
    if o:
        rows.append([name, o.get("usage_pct_of_limit"), o.get("anon_pct_of_limit"), o.get("pgmajfault_per_s"), o["p99_ms"]])
o = load("p3", "p3")
if o:
    for key, what in (("before_stop", "the 5 s before it stopped"), ("after_stop", "the 10 s after it stopped")):
        v = o["growth"]["windows"].get(key)
        if v:
            rows.append([f"{NAMES['p3']}, {what} ({v['from_s']}-{v['to_s']} s)", v["usage_pct_of_limit"], v["anon_pct_of_limit"], v["pgmajfault_per_s"], v["p99_ms"]])
if rows:
    lines += ["## Section 4", "", "Jobs `s3-n1` and `s3-p3`.", "", table(["condition", "memory.current (% of limit)", "anon (% of limit)", "pgmajfault (/s)", "p99 (ms)"], rows), ""]

rows = []
for name, run in ((BASE, "base"), (NAMES["f1"], "f1")):
    o = load("f1", run)
    if o:
        rows.append([name, fmt(o.get("limit_mib"), 2), fmt(o["cache_mib"], 2), o["p99_ms"], o.get("pgmajfault_per_s")])
if rows:
    lines += ["## Section 5", "", "Job `s3-f1`.", "", table(["condition", "--memory (MiB)", "in-process cache (MiB)", "p99 (ms)", "pgmajfault (/s)"], rows), ""]

lines += ["## Numbers not for the article", ""]
summaries = sorted(glob.glob(os.path.join(S3, "*", "*", "summary.json")))
for e in sorted(glob.glob(os.path.join(R, "environment", "*.txt"))):
    kv = dict(l.rstrip("\n").split("=", 1) for l in open(e) if "=" in l)
    job = os.path.basename(e)[:-4]
    sw = sorted({(o["swap"]["max"], o["swap"]["current_max"]) for o in (json.load(open(f)) for f in glob.glob(os.path.join(S3, job[3:], "*", "summary.json"))) if "swap" in o})
    lines.append(f"- {job}: kernel {kv.get('kernel')}, lru_gen/enabled {kv.get('lru_gen_enabled')}, swapon --show [{kv.get('swapon')}], "
                 f"memory.swap.max / largest memory.swap.current {', '.join(f'{a} / {b}' for a, b in sw) or '-'}")
app = next((o["app"] for o in (json.load(open(f)) for f in summaries) if "app" in o), None)
if app:
    lines.append(f"- Python {app['python']}; " + ", ".join(f"{k} {v}" for k, v in app["packages"].items()))
invalid = [l.strip() for f in sorted(glob.glob(os.path.join(R, "invalid-*.txt"))) for l in open(f) if l.strip()]
if invalid:
    lines += ["", "## RUN INVALID", ""] + [f"- {l}" for l in invalid]
print("\n".join(lines))
