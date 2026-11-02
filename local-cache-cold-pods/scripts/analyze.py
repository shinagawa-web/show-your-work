import glob
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime

results, cond = sys.argv[1], sys.argv[2]
R = lambda *p: os.path.join(results, *p)


def kv(path):
    out = {}
    for line in open(path):
        p = line.split()
        if len(p) >= 2:
            out.setdefault(p[0], p[1])
    return out


params = dict(line.strip().split("=", 1) for line in open(R("params.txt")) if "=" in line and " " not in line.strip())
head = dict(x.split("=") for x in open(R("params.txt")).readline().split())
add = int(head["add"])
timeline = kv(R("timeline.txt"))
op = float(timeline["op"])
op_s = math.floor(op)
initial = set(open(R("initial-pods.txt")).read().split())
final = set(open(R("final-pods.txt")).read().split())

stats = {}
for f in glob.glob(R("stats", "*.jsonl")):
    rows = [json.loads(x) for x in open(f) if x.strip()]
    if rows:
        stats[rows[0]["pod"]] = rows

per = defaultdict(lambda: defaultdict(float))
COUNTERS = ["req", "hit", "miss", "db_start", "db_done", "dup",
            "errs", "pool_acquire_count", "pool_empty_acquire", "pool_empty_wait_ns", "pool_acquire_ns"]
new_first_req = None
for pod, rows in stats.items():
    grp = "old" if pod in initial else "new"
    prev = None
    for r in rows:
        s = round(r["t"]) - 1
        if prev is not None and round(prev["t"]) == s:
            for c in COUNTERS:
                if c.startswith("pool_") and not prev["ready"]:
                    continue
                d = r[c] - prev[c]
                per[s][c] += d
                per[s][grp + "_" + c] += d
            if grp == "new" and r["req"] - prev["req"] > 0 and (new_first_req is None or s < new_first_req):
                new_first_req = s
        per[s]["db_inflight"] += r["db_inflight"]
        per[s]["pool_acquired"] += r["pool_acquired"]
        per[s]["pool_max"] += r["pool_max"]
        prev = r

lat = defaultdict(list)
status_bad = defaultdict(int)
stop_ms = float(timeline.get("k6_interrupted", "inf")) * 1000
for line in open(R("k6", "k6.log")):
    p = line.split()
    if p and p[0] == "r" and int(p[1]) < stop_ms:
        s = int(p[1]) // 1000
        lat[s].append(float(p[3]))
        if p[4] != "200":
            status_bad[s] += 1

def ncpus(spec):
    n = 0
    for part in spec.split(","):
        a, _, b = part.partition("-")
        n += int(b or a) - int(a) + 1
    return n


cpu = {}
stalls = []
prev = None
for line in open(R("cpu.jsonl")):
    r = json.loads(line)
    if "error" in r:
        continue
    if prev:
        dt = r["t"] - prev["t"]
        s = math.floor(prev["t"] + dt / 2)
        ticks = sum(v[0] - prev["proc_stat"][c][0] for c, v in r["proc_stat"].items()) / dt
        if ticks < 0.75 * 100 * len(r["proc_stat"]):
            stalls.append((s, ticks))
        cpu[s] = {"pg": (r["pg_usec"] - prev["pg_usec"]) / dt / 1e4 / ncpus(params["PG_CPUS"]),
                  "node": (r["node_usec"] - prev["node_usec"]) / dt / 1e4 / ncpus(params["NODE_CPUS"])}
    prev = r


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(math.ceil(q * len(xs))) - 1)]


k6_end = max(lat) if lat else op_s
secs = [s for s in sorted(lat) if s in per and s < k6_end]
table = []
for s in secs:
    d = per[s]
    row = {"rel": s - op_s, "req": d["req"], "db": d["db_start"],
           "ratio": d["db_start"] / d["req"] if d["req"] else None,
           "old_hit": d["old_hit"] / d["old_req"] if d["old_req"] else None,
           "new_req": d["new_req"], "new_hit": d["new_hit"] / d["new_req"] if d["new_req"] else None,
           "new_db": d["new_db_start"], "dup": d["dup"],
           "k6_n": len(lat[s]), "p50": pct(lat[s], 0.5), "p99": pct(lat[s], 0.99), "bad": status_bad[s],
           "pool_wait_ms": d["pool_empty_wait_ns"] / d["pool_empty_acquire"] / 1e6 if d["pool_empty_acquire"] else 0.0,
           "pool_empty": d["pool_empty_acquire"], "inflight": d["db_inflight"],
           "pool_acq": d["pool_acquired"], "pool_max": d["pool_max"]}
    c = cpu.get(s)
    row.update({"pg_cpu": c["pg"], "node_cpu": c["node"]} if c else {"pg_cpu": None, "node_cpu": None})
    table.append(row)

with open(R("timeseries.tsv"), "w") as f:
    cols = list(table[0].keys())
    f.write("\t".join(cols) + "\n")
    for row in table:
        f.write("\t".join("" if row[c] is None else ("%.4f" % row[c] if isinstance(row[c], float) else str(row[c])) for c in cols) + "\n")

base = [r for r in table if -15 <= r["rel"] <= -1]
after = [r for r in table if r["rel"] >= 0]
b_p99 = statistics.median(r["p99"] for r in base)
b_p50 = statistics.median(r["p50"] for r in base)
b_req = statistics.mean(r["req"] for r in base)
b_db = statistics.mean(r["db"] for r in base)
b_old_hit = sum(per[op_s + r["rel"]]["old_hit"] for r in base) / sum(per[op_s + r["rel"]]["old_req"] for r in base)
b_pg = statistics.mean(r["pg_cpu"] for r in base if r["pg_cpu"] is not None)
reading = [r["rel"] for r in after if r["db"] >= 10]
window_end = (max(reading) if reading else 0) + 3
event = [r for r in after if r["rel"] <= window_end]
peak = max(after, key=lambda r: r["db"])
pool = lambda lo, hi: pct([x for s in range(op_s + lo, op_s + hi) for x in lat.get(s, [])], 0.99)
b_p99_pooled = pool(-15, 0)
a_p99_pooled = pool(0, 30)
p99_peak = max(event, key=lambda r: r["p99"])
threshold = max(2 * b_p99, b_p99 + 10)
p99_high = [r["rel"] for r in after if r["p99"] > threshold]
pool_wait = [r["rel"] for r in after if r["pool_wait_ms"] >= 1.0]
pg_full = [r["rel"] for r in after if r["pg_cpu"] is not None and r["pg_cpu"] >= 90]

fill = None
if new_first_req is not None:
    rows = [r for r in table if op_s + r["rel"] >= new_first_req]
    for i, r in enumerate(rows):
        win = rows[i:i + 3]
        if len(win) == 3 and all(w["new_hit"] is not None and w["new_hit"] >= 0.99 * b_old_hit for w in win):
            fill = r["rel"] - (new_first_req - op_s)
            break

new_pods = sorted(p for p in stats if p not in initial)
new_db_total = sum(stats[p][-1]["db_start"] for p in new_pods)
new_dup_total = sum(stats[p][-1]["dup"] for p in new_pods)
new_req_total = sum(stats[p][-1]["req"] for p in new_pods)
excess_db = sum(r["db"] for r in after) - b_db * len(after)

ready_at = {}
gone_at = {}
seen = {}
for line in open(R("pods.jsonl")):
    r = json.loads(line)
    if "error" in r:
        continue
    names = set()
    for p in r["pods"]:
        names.add(p["pod"])
        seen.setdefault(p["pod"], r["t"])
        if p["ready"] and not p["deleting"] and p["pod"] not in ready_at:
            ready_at[p["pod"]] = r["t"]
        if p["deleting"] and p["pod"] not in gone_at:
            gone_at[p["pod"]] = r["t"]
new_ready = sorted(ready_at[p] - op for p in new_pods if p in ready_at)
old_deleted = sorted(gone_at[p] - op for p in initial if p in gone_at)

if os.path.exists(R("k6", "summary.json")):
    m = json.load(open(R("k6", "summary.json")))["metrics"]
    dropped = m.get("dropped_iterations", {}).get("values", {}).get("count", 0)
    iters = m["iterations"]["values"]["count"]
else:
    dropped, iters = 0, sum(len(v) for v in lat.values())
bad_total = sum(status_bad.values())
rollout = float(timeline["rollout_done"]) - op if "rollout_done" in timeline else None

invalid = []
vm_stalls = [s - op_s for s, _ in stalls if op_s - 15 <= s <= op_s + window_end]
if vm_stalls:
    invalid.append("the VM got less than 75%% of its CPU time from the host at seconds %s after the operation" % vm_stalls)
if "k6_incomplete" in timeline:
    invalid.append("k6 did not complete")
if b_old_hit < 0.98:
    invalid.append("hit rate of the existing Pods before the operation is %.4f (< 0.98): caches were not filled" % b_old_hit)
if dropped > 0.01 * (iters + dropped):
    invalid.append("dropped_iterations %d is over 1%% of the iterations" % dropped)
restarted = cond.startswith("restart")
if restarted and final & initial:
    invalid.append("Pods from before the restart remain: %s" % sorted(final & initial))
if restarted and (rollout is None or not any(l.startswith("rollout_done ") and l.split()[-1] == "exit=0" for l in open(R("timeline.txt")))):
    invalid.append("rollout status did not succeed")

frac = add / (4 + add) if add else None
out = {
    "cond": cond, "params": params, "add": add,
    "baseline": {"req_per_s": b_req, "db_per_s": b_db, "old_hit_rate": b_old_hit, "p50_ms": b_p50, "p99_ms": b_p99,
                 "pg_cpu_pct": b_pg},
    "peak_db": {"rel_s": peak["rel"], "db_per_s": peak["db"], "req_per_s": peak["req"], "ratio": peak["ratio"],
                "predicted_ratio": frac, "new_share": peak["new_req"] / peak["req"] if peak["req"] else None,
                "new_hit": peak["new_hit"]},
    "p99_pooled_ms": {"before_15s": b_p99_pooled, "after_30s": a_p99_pooled},
    "window_end_s": window_end,
    "p99_peak": {"rel_s": p99_peak["rel"], "p99_ms": p99_peak["p99"], "p50_ms": p99_peak["p50"]},
    "p99_rise_threshold_ms": threshold, "p99_above_threshold_s": p99_high,
    "pool_wait_ge_1ms_s": pool_wait, "pg_cpu_ge_90_s": pg_full,
    "new_pods": new_pods, "new_db_total": new_db_total, "new_dup_total": new_dup_total,
    "new_req_total": new_req_total,
    "excess_db_after_op": excess_db,
    "fill_s": fill, "new_ready_rel_s": new_ready, "old_deleting_rel_s": old_deleted, "rollout_s": rollout,
    "dropped_iterations": dropped, "iterations": iters, "non_200": bad_total,
    "node_cpu_peak_pct": max((r["node_cpu"] for r in after if r["node_cpu"] is not None), default=None),
    "pg_cpu_peak_pct": max((r["pg_cpu"] for r in after if r["pg_cpu"] is not None), default=None),
    "final_pods_equal_initial": final == initial if add else None,
    "invalid": invalid,
}
json.dump(out, open(R("metrics.json"), "w"), indent=1)


def f(x, fmt="%.1f"):
    return "-" if x is None else fmt % x


print("## %s" % cond)
print()
for msg in invalid:
    print("RUN INVALID: " + msg)
print("```")
print("params: " + " ".join("%s=%s" % kv for kv in params.items()))
print("baseline (15 s before the operation): req/s %.0f, DB reads/s %.1f, hit rate of existing Pods %.4f, p50 %.1f ms, p99 %.1f ms, DB CPU %.0f%%"
      % (b_req, b_db, b_old_hit, b_p50, b_p99, b_pg))
print("peak DB reads/s: %.0f at +%ds, requests/s %.0f, ratio %s (N/(4+N) = %s)"
      % (peak["db"], peak["rel"], peak["req"], f(peak["ratio"], "%.3f"), f(frac, "%.3f")))
print("in that second: share of requests to the new Pods %s, hit rate of the new Pods %s"
      % (f(out["peak_db"]["new_share"], "%.3f"), f(peak["new_hit"], "%.3f")))
print("p99 of all requests in the 15 s before the operation %.1f ms, in the 30 s from the operation %.1f ms"
      % (b_p99_pooled, a_p99_pooled))
print("DB reads by the new Pods (total until the end of the load): %d, of which overlapped with an in-flight read of the same key in the same Pod: %d"
      % (new_db_total, new_dup_total))
print("seconds until the new Pods' hit rate reached 99%% of the existing Pods': %s" % f(fill, "%d"))
print("p99 peak from the operation until 3 s after the last second with >= 10 DB reads (+%ds): %.1f ms at +%ds (p50 %.1f ms)"
      % (window_end, p99_peak["p99"], p99_peak["rel"], p99_peak["p50"]))
print("seconds with p99 > %.1f ms: %s" % (threshold, p99_high))
print("seconds with mean pgxpool empty-acquire wait >= 1 ms: %s" % pool_wait)
print("seconds with DB container CPU >= 90%%: %s" % pg_full)
print("new Pods Ready at (s after the operation): %s" % ", ".join("%.1f" % x for x in new_ready))
if rollout is not None and restarted:
    print("rollout restart took %.1f s (kubectl rollout status); old Pods marked for deletion at: %s"
          % (rollout, ", ".join("%.1f" % x for x in old_deleted)))
print("k6: iterations %d, dropped_iterations %d, non-200 %d" % (iters, dropped, bad_total))
print("node CPU peak %s%% of its CPUs, DB CPU peak %s%% of its CPUs" % (f(out["node_cpu_peak_pct"], "%.0f"), f(out["pg_cpu_peak_pct"], "%.0f")))
if add:
    print("Pods after scaling back to 4 are the initial 4: %s" % (final == initial))
print("```")
print()
print("| s | req/s | DB reads/s | ratio | new hit | p50 ms | p99 ms | pool wait ms | DB CPU % | node CPU % |")
print("|---|---|---|---|---|---|---|---|---|---|")
for r in table:
    if -5 <= r["rel"]:
        print("| %+d | %.0f | %.0f | %s | %s | %s | %s | %.1f | %s | %s |" % (
            r["rel"], r["req"], r["db"], f(r["ratio"], "%.3f"), f(r["new_hit"], "%.3f"), f(r["p50"]), f(r["p99"]),
            r["pool_wait_ms"], f(r["pg_cpu"], "%.0f"), f(r["node_cpu"], "%.0f")))
