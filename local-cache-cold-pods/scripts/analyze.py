import glob
import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

results, cond = sys.argv[1], sys.argv[2]
R = lambda *p: os.path.join(results, *p)
LATER_SECOND = 50


def kv(path):
    out = {}
    for line in open(path):
        p = line.split()
        if len(p) >= 2:
            out.setdefault(p[0], p[1])
    return out


def ncpus(spec):
    n = 0
    for part in spec.split(","):
        a, _, b = part.partition("-")
        n += int(b or a) - int(a) + 1
    return n


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(math.ceil(q * len(xs))) - 1)]


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def rel(t):
    return None if t is None else round(t - op, 3)


def iso(s):
    s = re.sub(r"(\.\d{6})\d*", r"\1", s).replace("Z", "+00:00")
    return datetime.fromisoformat(s).timestamp()


params = dict(line.strip().split("=", 1) for line in open(R("params.txt")) if "=" in line and " " not in line.strip())
head = dict(x.split("=") for x in open(R("params.txt")).readline().split())
timeline = kv(R("timeline.txt"))
op = float(timeline["op"])
stop = float(next(x.split()[1] for x in open(R("timeline.txt")) if x.startswith("stop ")))
interrupted = float(timeline.get("k6_interrupted", stop))
op_s = math.floor(op)

COUNTERS = ["req", "local_hit", "redis_hit", "redis_absent", "redis_failed", "redis_set_ok", "redis_set_failed", "miss", "db_start"]
per = defaultdict(lambda: defaultdict(float))
window = defaultdict(float)
for f in glob.glob(R("stats", "*.jsonl")):
    rows = [json.loads(x) for x in open(f) if x.strip()]
    prev = None
    for r in rows:
        s = round(r["t"]) - 1
        vals = {c: r[c] for c in COUNTERS}
        base = prev or {k: 0 for k in vals}
        for k, v in vals.items():
            d = v - base[k]
            per[s][k] += d
            if op <= r["t"] - 0.5 <= stop:
                window[k] += d
        per[s]["db_inflight"] += r["db_inflight"]
        prev = vals

lat = defaultdict(list)
bad = defaultdict(int)
stop_ms = interrupted * 1000
for line in open(R("k6", "k6.log")):
    p = line.split()
    if len(p) == 5 and p[0] == "r" and int(p[1]) < stop_ms:
        start_ms, elapsed = int(p[1]), int(p[2])
        s = (start_ms + elapsed) // 1000
        ok = p[4] == "200"
        v = float(p[3]) if ok else float(elapsed)
        lat[s].append(v)
        if not ok:
            bad[s] += 1

cpu = {}
prev = None
for line in open(R("cpu.jsonl")):
    r = json.loads(line)
    if "error" in r:
        continue
    if prev:
        dt = r["t"] - prev["t"]
        s = math.floor(prev["t"] + dt / 2)
        cpu[s] = {"pg": (r["pg_usec"] - prev["pg_usec"]) / dt / 1e4 / ncpus(params["PG_CPUS"]),
                  "node": (r["node_usec"] - prev["node_usec"]) / dt / 1e4 / ncpus(params["NODE_CPUS"])}
    prev = r

win_secs = [s for s in sorted(set(lat) | set(cpu)) if op_s <= s <= math.floor(stop)]
base_secs = [s for s in sorted(lat) if op_s - 10 <= s < op_s]
all_lat = [x for s in win_secs for x in lat.get(s, [])]

with open(R("timeseries.tsv"), "w") as f:
    cols = ["rel", "req", "local_hit", "redis_hit", "redis_absent", "redis_failed", "miss", "db_start", "db_inflight",
            "set_ok", "set_failed", "k6_n", "bad", "p50", "p99", "pg_cpu", "node_cpu"]
    print("\t".join(cols), file=f)
    for s in sorted(lat):
        d = per.get(s, {})
        c = cpu.get(s, {})
        row = [s - op_s, d.get("req", 0), d.get("local_hit", 0), d.get("redis_hit", 0), d.get("redis_absent", 0),
               d.get("redis_failed", 0), d.get("miss", 0), d.get("db_start", 0), d.get("db_inflight", 0),
               d.get("redis_set_ok", 0), d.get("redis_set_failed", 0), len(lat[s]), bad[s],
               pct(lat[s], 0.5), pct(lat[s], 0.99), c.get("pg"), c.get("node")]
        print("\t".join("" if v is None else (("%.1f" % v) if isinstance(v, float) else str(v)) for v in row), file=f)


def reads(s):
    return per.get(s, {}).get("db_start", 0)


def db_second(s):
    return {"second": s - op_s, "reads": reads(s)}


events = []
for f in glob.glob(R("stats", "*.events")):
    pod = os.path.basename(f).rsplit(".", 1)[0]
    for line in open(f):
        if line.strip():
            r = json.loads(line)
            r["pod"] = pod
            events.append(r)
events.sort(key=lambda r: r["t"])

intervals = defaultdict(list)
open_down = {}
for r in events:
    if not op - 1 <= r["t"] <= stop + 1:
        continue
    if r["ev"] == "probe_down":
        open_down.setdefault(r["pod"], r)
    elif r["ev"] == "probe_up" and r["pod"] in open_down:
        intervals[r["pod"]].append((open_down.pop(r["pod"])["last_ok"], r["t"]))
for pod, d in open_down.items():
    intervals[pod].append((d["last_ok"], stop))
longest = [max(v, key=lambda x: x[1] - x[0]) for v in ([x for x in v if x[1] >= op] for v in intervals.values()) if v]
groups = []
for start, end in sorted(x for v in intervals.values() for x in v if x[1] >= op):
    if groups and start <= groups[-1][1]:
        groups[-1][1] = max(groups[-1][1], end)
    else:
        groups.append([start, end])

def log_lines(pod, container):
    path = R("logs", "%s.%s.log" % (pod, container))
    if not os.path.exists(path):
        return []
    seen, out = set(), []
    for line in open(path, errors="replace"):
        if line in seen:
            continue
        seen.add(line)
        ts, _, rest = line.partition(" ")
        try:
            out.append((iso(ts), rest.rstrip()))
        except ValueError:
            pass
    out.sort(key=lambda x: x[0])
    return out


def first(lines, needle, after=0):
    for t, msg in lines:
        if t >= after and needle in msg:
            return t, msg
    return None, None


steps = {}
redis_pod = None
if head["redis"].startswith("single"):
    redis_pod = "redis-0"
elif head["redis"] == "sentinel":
    before = open(R("redis-before.txt")).read() if os.path.exists(R("redis-before.txt")) else ""
    redis_pod = next(iter(re.findall(r"== (\S+)\n(?:[^=]*\n)*?role:master", before)), None)
if redis_pod and head["op"] in ("redis-restart", "master-delete"):
    lines = log_lines(redis_pod, "redis")
    term, _ = first(lines, "Received SIGTERM", op)
    save_start, _ = first(lines, "Saving the final RDB snapshot before exiting", op)
    bye, _ = first(lines, "ready to exit", op)
    start_t, _ = first(lines, "Redis is starting", op)
    accept, _ = first(lines, "Ready to accept connections", op)
    steps = {"sigterm": rel(term), "save_start": rel(save_start), "exit": rel(bye), "process_start": rel(start_t),
             "accepting": rel(accept)}
    if longest:
        steps["last_pod_back"] = rel(max(x[1] for x in longest))

sentinel = {}
if head["redis"] == "sentinel":
    lines = sorted(x for i in range(3) for x in log_lines("redis-ha-server-%d" % i, "sentinel"))
    for name, needle in (("+sdown", "+sdown master"), ("+switch-master", "+switch-master")):
        t, _ = first(lines, needle, op - 1)
        if t is not None:
            sentinel[name] = rel(t)
    sentinel["events"] = [[rel(t), msg] for t, msg in lines
                          if t >= op - 1 and any(n in msg for n in ("+sdown master", "+odown", "+switch-master", "+failover-end", "-failover-abort"))]

k6 = json.load(open(R("k6", "summary.json")))["metrics"] if os.path.exists(R("k6", "summary.json")) else {}
dropped = k6.get("dropped_iterations", {}).get("values", {}).get("count", 0)
dbsize = lambda path: dict(re.findall(r"== (\S+)\n(?:\(integer\) )?(\S+)", open(path).read())) if os.path.exists(path) else {}

out = {
    "cond": cond,
    "rate": int(params["RATE"]),
    "observed_seconds": stop - op,
    "stopped_seconds": max(x[1] for x in longest) - min(x[0] for x in longest) if longest else 0.0,
    "stopped_intervals": [{"start": rel(a), "end": rel(b), "seconds": b - a} for a, b in groups],
    "master_before": redis_pod if head["redis"] == "sentinel" else None,
    "steps": steps,
    "sentinel": sentinel,
    "req": window["req"],
    "miss": window["miss"],
    "miss_redis_failed": window["redis_failed"],
    "miss_redis_absent": window["redis_absent"],
    "db_reads_before": mean([reads(s) for s in base_secs]),
    "db_reads_peak": db_second(max(win_secs, key=reads)),
    "db_later": db_second(op_s + LATER_SECOND),
    "p99_ms": pct(all_lat, 0.99),
}

problems = []
if "k6_incomplete" in timeline:
    problems.append("k6 did not finish (%s)" % next(x.split()[2] for x in open(R("timeline.txt")) if x.startswith("k6_incomplete")))
if dropped:
    problems.append("k6 dropped %d requests" % dropped)
if redis_pod:
    sizes = set(dbsize(R("redis-before.txt")).values())
    if head["redis"] == "sentinel" and sizes != {params["KEYS"]}:
        problems.append("Redis held %s keys before the operation, expected %s on every server" % (sizes, params["KEYS"]))
    if head["redis"] != "sentinel" and sizes != {params["KEYS"]}:
        problems.append("Redis held %s keys before the operation, expected %s" % (sizes, params["KEYS"]))
if head["op"] == "master-delete":
    deleted = re.findall(r'pod "(\S+)" deleted', open(R("operation.txt")).read())
    if deleted != [redis_pod]:
        problems.append("deleted %s, but the master before the operation was %s" % (deleted, redis_pod))
out["problems"] = problems
json.dump(out, open(R("metrics.json"), "w"), indent=1)
for p in problems:
    print("RUN INVALID: " + p)
print("# %s\n" % cond)
print("```json\n%s\n```" % json.dumps(out, indent=1))
