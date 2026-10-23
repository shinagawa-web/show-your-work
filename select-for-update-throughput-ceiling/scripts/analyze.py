#!/usr/bin/env python3
import json
import os
import statistics
import sys
from collections import defaultdict

out = sys.argv[1]
run = json.load(open(os.path.join(out, "run.json")))
reqs = [json.loads(line) for line in open(os.path.join(out, "requests.jsonl"))]
spans = [json.loads(line) for line in open(os.path.join(out, "spans.jsonl"))]
t0 = run["t0_ns"]
dur = int(run["duration"])
invalid = []


def ms(ns):
    return ns / 1e6


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(-(-q * len(xs) // 1)) - 1))]


def med(xs):
    return statistics.median(xs) if xs else None


def f(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"


def dist(xs):
    return f"n={len(xs)} mean={f(statistics.fmean(xs) if xs else None)} p50={f(pct(xs, 0.5))} p99={f(pct(xs, 0.99))} max={f(max(xs) if xs else None)}"


by_trace = defaultdict(list)
for s in spans:
    by_trace[s["trace_id"]].append(s)


def sql_spans(r):
    return sorted((s for s in by_trace.get(r["trace_id"], []) if s["parent_id"] is not None and s["name"] != r["root"]),
                  key=lambda s: s["start_ns"])


lines = []
p = lines.append
p(f"settings: arrivals {run['rate']:g}/s Poisson for {run['duration']:g}s, pool {run['pool']}, work {run['work']:g}s {run['work_at']} the transaction"
  + (f", plain SELECT requests {run['plain_rate']:g}/s on the same pool" if run["plain_rate"] else ""))
p(f"arrivals: {run['arrivals']}")

lag = [ms(r["start_ns"] - r["sched_ns"]) for r in reqs]
p(f"dispatch lag from scheduled arrival to root span start, ms: {dist(lag)}")
if lag and max(lag) > 50:
    invalid.append(f"dispatch lag up to {max(lag):.1f} ms")

summary = {"settings": {k: run[k] for k in ("rate", "pool", "work", "work_at", "plain_rate", "duration")}}

for kind, root in (("order", "POST /orders"), ("plain", "GET /items")):
    rs = [r for r in reqs if r["kind"] == kind]
    if not rs:
        continue
    for r in rs:
        r["root"] = root
    n_arr = run["arrivals"].get(kind if kind == "plain" else "order", 0)
    if len(rs) != n_arr:
        invalid.append(f"{kind}: {len(rs)} records for {n_arr} arrivals")
    status = defaultdict(int)
    for r in rs:
        status[r["status"]] += 1
    ok = [r for r in rs if r["status"] == "ok"]
    timeouts = [r for r in rs if r["status"] == "pool_timeout"]
    errors = [r for r in rs if r["status"] == "error"]
    if errors:
        invalid.append(f"{kind}: {len(errors)} errors, first {errors[0].get('error')}")
    if status.get("sold_out"):
        invalid.append(f"{kind}: {status['sold_out']} sold out")

    p("")
    p(f"## {kind} requests ({root})")
    p(f"status: {dict(status)}")

    per_sec = [0] * dur
    after = 0
    for r in ok:
        sec = (r["commit_ns"] - t0) // 1_000_000_000
        if 0 <= sec < dur:
            per_sec[sec] += 1
        elif sec >= dur:
            after += 1
    p(f"COMMITs per second during the {dur}s of arrivals: {per_sec}")
    p(f"COMMITs per second, mean over the {dur}s: {sum(per_sec) / dur:.2f}; COMMITs after the arrivals ended: {after}")

    resp = [ms(r["end_ns"] - r["sched_ns"]) for r in ok]
    p(f"response time from scheduled arrival, ms, excluding pool timeouts: p50={f(pct(resp, 0.5))} p99={f(pct(resp, 0.99))} max={f(max(resp) if resp else None)} (n={len(resp)})")
    p(f"requests that hit the pool timeout: {len(timeouts)}")
    if timeouts:
        first = min(timeouts, key=lambda r: r["sched_ns"])
        p(f"first arrival that hit the pool timeout: {(first['sched_ns'] - t0) / 1e9:.2f}s after the arrivals started")

    p("per arrival second: arrived, pool timeouts, median connection wait ms, median response ms (completed only)")
    rows = []
    for sec in range(dur):
        group = [r for r in rs if (r["sched_ns"] - t0) // 1_000_000_000 == sec]
        g_ok = [r for r in group if r["status"] == "ok"]
        cw = med([ms(r["conn_ns"] - r["start_ns"]) for r in g_ok])
        rt = med([ms(r["end_ns"] - r["sched_ns"]) for r in g_ok])
        to = sum(1 for r in group if r["status"] == "pool_timeout")
        rows.append({"sec": sec, "arrived": len(group), "timeouts": to, "conn_wait_ms": cw, "response_ms": rt})
        p(f"  {sec:>2}s  {len(group):>3}  {to:>3}  {f(cw):>9}  {f(rt):>9}")

    names = defaultdict(list)
    gaps, hold_last, last_to_commit, hold_commit, fu_by_sec, fu_spans = [], [], [], [], defaultdict(list), []
    missing = 0
    for r in ok:
        ss = sql_spans(r)
        expect = 3 if kind == "order" else 1
        if len(ss) != expect:
            missing += 1
            continue
        for s in ss:
            key = s["name"] + (" ... FOR UPDATE" if "FOR UPDATE" in s["attributes"].get("db.statement", "") else "")
            names[key].append(ms(s["end_ns"] - s["start_ns"]))
        root_span = next((s for s in by_trace[r["trace_id"]] if s["parent_id"] is None), None)
        if root_span:
            gaps.append(ms(ss[0]["start_ns"] - root_span["start_ns"]))
        last_end = ss[-1]["end_ns"]
        last_to_commit.append(ms(r["commit_ns"] - last_end))
        if kind == "order":
            fu = ss[0]
            fu_spans.append((fu["start_ns"], fu["end_ns"]))
            hold_last.append(ms(last_end - fu["end_ns"]))
            hold_commit.append(ms(r["commit_ns"] - fu["end_ns"]))
            fu_by_sec[(r["sched_ns"] - t0) // 1_000_000_000].append(ms(fu["end_ns"] - fu["start_ns"]))
    if missing:
        invalid.append(f"{kind}: {missing} completed requests without the expected SQL spans")
    p("SQL span durations, ms:")
    for k in sorted(names):
        p(f"  {k}: {dist(names[k])}")
    p(f"trace gap from root span start to the first SQL span start, ms: {dist(gaps)}")
    if kind == "order":
        p(f"FOR UPDATE span end to last SQL span end, ms: {dist(hold_last)}")
        p(f"FOR UPDATE span end to conn.commit() returned, ms: {dist(hold_commit)}")
        hc = med(hold_commit)
        if hc:
            p(f"1 / median(FOR UPDATE span end to conn.commit() returned): {1000 / hc:.2f}/s")
        fu_spans.sort()
        overtaken = [sum(1 for _, e in fu_spans[i + 1:] if e < end) for i, (_, end) in enumerate(fu_spans)]
        p(f"FOR UPDATE spans that ended after a FOR UPDATE span that started later: {sum(1 for k in overtaken if k)} of {len(fu_spans)}; later-started spans that ended first, per span: {dist(overtaken)}")
        p("median FOR UPDATE span per arrival second, ms: " + " ".join(f"{s}:{f(med(fu_by_sec.get(s, [])), 0)}" for s in range(dur)))
    p(f"last SQL span end to conn.commit() returned, ms: {dist(last_to_commit)}")

    summary[kind] = {
        "status": dict(status), "commits_per_sec": per_sec, "commits_after": after,
        "p50_ms": pct(resp, 0.5), "p99_ms": pct(resp, 0.99), "pool_timeouts": len(timeouts),
        "per_arrival_sec": rows,
        "span_ms": {k: {"p50": pct(v, 0.5), "p99": pct(v, 0.99), "max": max(v)} for k, v in names.items()},
        "hold_last_sql_ms": {"p50": pct(hold_last, 0.5), "p99": pct(hold_last, 0.99)} if hold_last else None,
        "hold_commit_ms": {"p50": pct(hold_commit, 0.5), "p99": pct(hold_commit, 0.99)} if hold_commit else None,
        "last_sql_to_commit_ms": {"p50": pct(last_to_commit, 0.5), "p99": pct(last_to_commit, 0.99)},
    }

ok_orders = sum(1 for r in reqs if r["kind"] == "order" and r["status"] == "ok")
p("")
p(f"database after the run: stock {run['db_stock_after']}, orders {run['db_orders_after']}; completed order requests {ok_orders}")
if run["db_orders_after"] != ok_orders:
    invalid.append("orders in the database differ from completed order requests")
if run["db_stock_after"] <= 0:
    invalid.append("stock ran out")
if run["spans_exported"] != len(spans):
    invalid.append(f"{run['spans_exported']} spans exported, {len(spans)} in spans.jsonl")

cpu = []
for line in list(open(os.path.join(out, "cpu.tsv")))[1:]:
    t, u = line.split()
    cpu.append((int(t), int(u)))
win = [(t, u) for t, u in cpu if t0 <= t <= t0 + dur * 1_000_000_000]
if len(win) < 2:
    invalid.append("no PostgreSQL CPU samples during the arrivals")
else:
    (ta, ua), (tb, ub) = win[0], win[-1]
    mean = (ub - ua) / ((tb - ta) / 1000) * 100
    steps = [(u2 - u1) / ((t2 - t1) / 1000) * 100 for (t1, u1), (t2, u2) in zip(win, win[1:])]
    p(f"PostgreSQL container CPU during the arrivals, % of one CPU: mean {mean:.1f}, max per sample {max(steps):.1f} ({len(steps)} samples)")
    summary["postgres_cpu_pct"] = {"mean": mean, "max_sample": max(steps)}

for reason in invalid:
    p(f"RUN INVALID: {reason}")
summary["invalid"] = invalid
json.dump(summary, open(os.path.join(out, "analysis.json"), "w"), indent=1)
print("\n".join(lines))
sys.exit(1 if invalid else 0)
