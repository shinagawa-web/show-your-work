#!/usr/bin/env python3
import csv
import json
import os
import sys

results = sys.argv[1]
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
conds = list(csv.DictReader(open(os.path.join(here, "conditions.tsv")), delimiter="\t"))
work_at = {"inside": "inside the transaction", "before": "before the transaction",
           "after-commit": "after the transaction with the lock commits"}

runs, invalid = {}, []
for c in conds:
    c["job"] = f"{c['section']} ({c['name']})"
    d = os.path.join(results, c["name"])
    if not os.path.isdir(d):
        continue
    path = os.path.join(d, "analysis.json")
    if not os.path.exists(path):
        invalid.append(f"job `{c['job']}`: no analysis")
        continue
    a = json.load(open(path))
    invalid += [f"job `{c['job']}`: {r}" for r in a["invalid"]]
    pg = os.path.join(d, "postgres.txt")
    a["postgres"] = open(pg).read().split("\n") if os.path.exists(pg) else []
    runs[c["name"]] = (c, a)


def f(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"


def settings(c):
    s = f"arrivals {c['rate']}/s Poisson for 30 s, pool {c['pool']}, work {c['work']} s {work_at[c['work_at']]}"
    if c["plain_rate"] != "0":
        s += f", plain SELECT requests {c['plain_rate']}/s on the same pool"
    return s


def job_line(c):
    return f"Job `{c['job']}`: {settings(c)}"


def table(rows, head=("Measure", "Value")):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return out


lines = ["# select-for-update-throughput-ceiling", ""]
pg = next((a["postgres"] for _, a in runs.values() if len(a["postgres"]) >= 2), None)
if pg:
    lines += [f"{' '.join(pg[0].split()[:2])}, max_connections {pg[1]}", ""]
for r in invalid:
    lines += [f"RUN INVALID: {r}", ""]

if "p1" in runs:
    c, a = runs["p1"]
    o = a["order"]
    hold = o["hold_commit_ms"]["p50"]
    at = o["response_at"]
    lines += ["## 3. Completions and response time at the peak", "", job_line(c), ""]
    lines += table([
        ("COMMITs per second, mean over the 30 s of arrivals", f(o["commits_mean"], 2)),
        ("Lock hold, `SELECT ... FOR UPDATE` span end to `conn.commit()` returned, median", f"{f(hold)} ms"),
        ("1 / median lock hold", f"{f(1000 / hold, 2)}/s"),
        *[(f"Response time of the first order that arrived at or after {s} s (arrived at {at[str(s)]['arrival_s']:.2f} s)",
           f"{f(at[str(s)]['response_ms'])} ms") for s in (10, 29) if str(s) in at],
        ("Response time p99", f"{f(o['p99_ms'])} ms"),
    ])
    sp = o["span_ms"]
    lines += ["", "## 4. Telling it apart in your own environment", "", job_line(c), ""]
    lines += table([
        ("`SELECT ... FOR UPDATE` span, median", f"{f(sp['SELECT ... FOR UPDATE']['p50'])} ms"),
        ("`UPDATE` span, median", f"{f(sp['UPDATE']['p50'])} ms"),
        ("`INSERT` span, median", f"{f(sp['INSERT']['p50'])} ms"),
        ("Lock hold, `SELECT ... FOR UPDATE` span end to the last SQL span end in the same transaction, median",
         f"{f(o['hold_last_sql_ms']['p50'])} ms"),
        ("Same lock hold, p99", f"{f(o['hold_last_sql_ms']['p99'])} ms"),
        ("Connection wait, median", f"{f(o['conn_wait_ms']['p50'])} ms"),
    ])
    lines.append("")

fixes = [runs[n] for n in ("f1", "f2") if n in runs]
if fixes:
    lines += ["## 5. Fixes", ""]
    lines += table([(f"`{c['job']}`", settings(c), f(a["order"]["commits_mean"], 2), f"{f(a['order']['p99_ms'])} ms")
                    for c, a in fixes],
                   ("Job", "Settings", "COMMITs per second, mean over the 30 s of arrivals", "Response time p99"))
    lines.append("")

unused = [(c, a) for c, a in runs.values() if c["section"] == "unused"]
if runs:
    lines += ["## Numbers not used", ""]
if unused:
    rows = []
    for c, a in unused:
        for kind in ("order", "plain"):
            if kind not in a:
                continue
            o = a[kind]
            hold = (o.get("hold_commit_ms") or {}).get("p50")
            rows.append((f"`{c['job']}`", settings(c), kind, f(o["commits_mean"], 2),
                         f"{f(hold)} ms" if hold else "-", f"{f(1000 / hold, 2)}/s" if hold else "-",
                         f"{f(o['p99_ms'])} ms", o["pool_timeouts"]))
    lines += table(rows, ("Job", "Settings", "Requests", "COMMITs per second, mean", "Lock hold to `conn.commit()`, median",
                          "1 / median lock hold", "Response time p99", "Pool timeouts"))
    lines.append("")
if runs:
    rows = []
    for c, a in runs.values():
        ov = a["order"].get("overtaken")
        cpu = a.get("postgres_cpu_pct")
        rows.append((f"`{c['job']}`", f"{f(cpu['mean'])}%" if cpu else "-", f"{f(cpu['max_sample'])}%" if cpu else "-",
                     f"{ov['spans']} of {ov['of']}" if ov else "-"))
    lines += table(rows, ("Job", "PostgreSQL container CPU, mean, % of one CPU", "Max per sample",
                          "`SELECT ... FOR UPDATE` spans that ended after one that started later"))
    lines.append("")

print("\n".join(lines).rstrip())
sys.exit(1 if invalid or not runs else 0)
