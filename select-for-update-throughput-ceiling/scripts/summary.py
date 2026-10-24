#!/usr/bin/env python3
import csv
import json
import os
import sys

# Usage: summary.py RESULTS [CONDITION ...]
# Every named condition, or every condition in conditions.tsv when none is named, must have
# RESULTS/<condition>/analysis.json; otherwise the missing conditions are named and the exit code is 1.
results = sys.argv[1]
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
conds = list(csv.DictReader(open(os.path.join(here, "conditions.tsv")), delimiter="\t"))
names = [c["name"] for c in conds]
expected = sys.argv[2:] or names
unknown = [n for n in expected if n not in names]
if unknown:
    sys.exit(f"unknown condition: {' '.join(unknown)} (one of: {' '.join(names)})")
conds = [c for c in conds if c["name"] in expected]
claims = {
    "baseline": "With arrivals below the ceiling, completions match arrivals and response time does not grow",
    "p2": "Adding connections does not raise COMMITs per second",
    "p3": "The ceiling is 1 / lock hold: doubling the hold halves the ceiling",
    "p4": "Other requests that use the same connection pool slow down too",
}
work_at = {"inside": "inside the transaction", "before": "before the transaction",
           "after-commit": "after the transaction with the lock commits"}

runs, invalid, missing = {}, [], []
for c in conds:
    c["job"] = f"{c['section']} ({c['name']})"
    d = os.path.join(results, c["name"])
    if not os.path.isdir(d):
        missing.append(c["name"])
        invalid.append(f"job `{c['job']}`: no {d}/")
        continue
    path = os.path.join(d, "analysis.json")
    if not os.path.exists(path):
        missing.append(c["name"])
        invalid.append(f"job `{c['job']}`: no {path}")
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
    lines += ["## 3. Completions and response time at the peak", "", job_line(c), ""]
    lines += table([
        ("COMMITs per second, mean over the 30 s of arrivals", f(o["commits_mean"], 2)),
        ("Lock hold, `SELECT ... FOR UPDATE` span end to `conn.commit()` returned, median",
         f"{f(o['hold_commit_ms']['p50'])} ms"),
        ("Response time p99", f"{f(o['p99_ms'])} ms"),
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
            rows.append((f"`{c['job']}`", settings(c), claims.get(c["name"], "-"), kind, f(o["commits_mean"], 2),
                         f"{f(hold)} ms" if hold else "-", f"{f(o['p99_ms'])} ms", o["pool_timeouts"]))
    lines += table(rows, ("Job", "Settings", "Claim checked", "Requests", "COMMITs per second, mean",
                          "Lock hold to `conn.commit()`, median", "Response time p99", "Pool timeouts"))
    lines.append("")
if runs:
    lines += ["The CPU columns check the claim that the ceiling appears while the database CPU usage stays low. "
              "The last column checks the claim that locks are not granted in arrival order.", ""]
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
if missing:
    print(f"missing conditions: {' '.join(missing)}", file=sys.stderr)
sys.exit(1 if invalid or not runs else 0)
