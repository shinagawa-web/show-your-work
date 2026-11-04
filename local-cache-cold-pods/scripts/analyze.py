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
ERR = ["loading", "refused", "timeout", "readonly", "other"]


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
stop_reason = next(x.split()[2] for x in open(R("timeline.txt")) if x.startswith("stop "))
interrupted = float(timeline.get("k6_interrupted", stop))
op_s = math.floor(op)

COUNTERS = ["req", "local_hit", "redis_hit", "redis_absent", "redis_failed", "redis_set_ok", "miss",
            "db_start", "db_done", "db_err", "dup", "errs"]
per = defaultdict(lambda: defaultdict(float))
window = defaultdict(float)
other_msgs = defaultdict(int)
for f in glob.glob(R("stats", "*.jsonl")):
    rows = [json.loads(x) for x in open(f) if x.strip()]
    prev = None
    other_before = {}
    last_other = {}
    for r in rows:
        s = round(r["t"]) - 1
        vals = {c: r[c] for c in COUNTERS}
        for group in ("get_err", "set_err"):
            for e in ERR:
                vals[group + "_" + e] = r[group][e]
        base = prev or {k: 0 for k in vals}
        for k, v in vals.items():
            d = v - base[k]
            per[s][k] += d
            if op <= r["t"] - 0.5 <= stop:
                window[k] += d
        per[s]["db_inflight"] += r["db_inflight"]
        if r["t"] < op:
            other_before = r.get("other_err", {})
        if r["t"] <= stop + 1:
            last_other = r.get("other_err", {})
        prev = vals
    for k, v in last_other.items():
        d = v - other_before.get(k, 0)
        if d:
            other_msgs[k] += d

lat = defaultdict(list)
by_start = defaultdict(list)
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
        if op * 1000 <= start_ms <= stop * 1000:
            by_start[int((start_ms - op * 1000) // 100)].append(elapsed)

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
hot = [s for s in win_secs if s in cpu and cpu[s]["node"] >= 95]
base_secs = [s for s in sorted(lat) if op_s - 10 <= s < op_s]
all_lat = [x for s in win_secs for x in lat.get(s, [])]
cool_lat = [x for s in win_secs if s not in hot for x in lat.get(s, [])]
base_lat = [x for s in base_secs for x in lat[s]]

with open(R("timeseries.tsv"), "w") as f:
    cols = ["rel", "req", "local_hit", "redis_hit", "redis_absent", "redis_failed", "miss", "db_start", "db_inflight",
            "set_ok", "get_err", "set_err", "k6_n", "bad", "p50", "p99", "pg_cpu", "node_cpu"]
    print("\t".join(cols), file=f)
    for s in sorted(lat):
        d = per.get(s, {})
        c = cpu.get(s, {})
        row = [s - op_s, d.get("req", 0), d.get("local_hit", 0), d.get("redis_hit", 0), d.get("redis_absent", 0),
               d.get("redis_failed", 0), d.get("miss", 0), d.get("db_start", 0), d.get("db_inflight", 0),
               d.get("redis_set_ok", 0), sum(d.get("get_err_" + e, 0) for e in ERR),
               sum(d.get("set_err_" + e, 0) for e in ERR), len(lat[s]), bad[s],
               pct(lat[s], 0.5), pct(lat[s], 0.99), c.get("pg"), c.get("node")]
        print("\t".join("" if v is None else (("%.1f" % v) if isinstance(v, float) else str(v)) for v in row), file=f)

events = []
for f in glob.glob(R("stats", "*.events")):
    pod = os.path.basename(f).rsplit(".", 1)[0]
    for line in open(f):
        if line.strip():
            r = json.loads(line)
            r["pod"] = pod
            events.append(r)
events.sort(key=lambda r: r["t"])
ev_win = [r for r in events if r["t"] >= op - 1 and r["t"] <= stop + 1]


def spans(down, up):
    out = defaultdict(list)
    open_down = {}
    for r in ev_win:
        if r["ev"] == down:
            open_down.setdefault(r["pod"], r)
        elif r["ev"] == up and r["pod"] in open_down:
            d = open_down.pop(r["pod"])
            out[r["pod"]].append((d["last_ok"], r["t"], d["kind"], d["err"]))
    for pod, d in open_down.items():
        out[pod].append((d["last_ok"], stop, d["kind"], d["err"] + " (still down at stop)"))
    return out


intervals = {pod: [x for x in v if x[1] >= op] for pod, v in spans("probe_down", "probe_up").items()}
longest = [max(v, key=lambda x: x[1] - x[0]) for v in intervals.values() if v]
flat = sorted(longest)
stopped = None
per_pod = []
short_spans = []
if flat:
    stopped = {"from": min(x[0] for x in flat) - op, "to": max(x[1] for x in flat) - op}
    stopped["seconds"] = stopped["to"] - stopped["from"]
    per_pod = [b - a for a, b, _, _ in flat]
    short_spans = sorted(round(x[0] - op, 2) for v in intervals.values() for x in v if x not in longest)
first_errors = sorted({(x[2], x[3]) for x in flat})
served = sorted(x for v in spans("down", "up").values() for x in v)
first_request_error = min((r["t"] for r in ev_win if r["ev"] == "down"), default=None)
last_request_error = max((x[1] for x in served), default=None)

polls = []
for line in open(R("pods.jsonl")):
    r = json.loads(line)
    if "pods" in r:
        polls.append(r)


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


def ready_after(pod, uid_not=None, after=0):
    for p in polls:
        if p["t"] < after:
            continue
        for x in p["pods"]:
            if x["pod"] == pod and x["ready"] and x.get("uid") != uid_not:
                return p["t"]
    return None


def uid_before(pod):
    for p in reversed(polls):
        if p["t"] < op:
            for x in p["pods"]:
                if x["pod"] == pod:
                    return x.get("uid")
    return None


steps = {}
redis_pod = None
load_seconds = None
if head["redis"].startswith("single"):
    redis_pod = "redis-0"
elif head["op"] == "sentinel-restart":
    redis_pod = "redis-ha-server-2"
elif head["redis"] == "sentinel":
    redis_pod = "redis-ha-server-0"
if redis_pod and head["op"] in ("redis-restart", "sentinel-restart", "sentinel-kill"):
    lines = log_lines(redis_pod, "redis")
    term, _ = first(lines, "Received SIGTERM", op)
    saved, _ = first(lines, "DB saved on disk", op)
    bye, _ = first(lines, "ready to exit", op)
    began = bye or op
    start_t, _ = first(lines, "Redis is starting", op)
    loaded, msg = first(lines, "DB loaded from disk", op)
    if msg:
        load_seconds = float(re.search(r"([\d.]+) seconds", msg).group(1))
    accept, _ = first(lines, "Ready to accept connections", op)
    ready = ready_after(redis_pod, uid_not=uid_before(redis_pod) if head["op"] != "sentinel-kill" else None, after=(accept or op))
    steps = {"sigterm": rel(term), "rdb_saved": rel(saved), "exit": rel(bye), "process_start": rel(start_t),
             "db_loaded": rel(loaded), "accepting": rel(accept), "pod_ready": rel(ready)}
    if flat:
        steps["first_pod_back"] = rel(min(x[1] for x in flat))
        steps["last_pod_back"] = rel(max(x[1] for x in flat))

sentinel = []
if head["redis"] == "sentinel":
    for i in range(3):
        for t, msg in log_lines("redis-ha-server-%d" % i, "sentinel"):
            if t >= op - 1 and re.search(r"\+sdown|-sdown|\+odown|-odown|\+try-failover|\+failover-state-|\+switch-master|\+failover-end|\+reboot|\+slave |\+promoted|-failover", msg):
                sentinel.append({"t": rel(t), "pod": i, "msg": msg.split(" ", 4)[-1][:110]})
    sentinel.sort(key=lambda x: x["t"])


def run_of(pred):
    best = cur = 0
    for b in range(0, (max(by_start) + 1) if by_start else 0):
        if pred(by_start.get(b, [])):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best / 10


stall_longest = run_of(lambda xs: bool(xs) and sorted(xs)[len(xs) // 2] >= 100)
slow = sum(1 for xs in by_start.values() for v in xs if v >= 100)
k6 = json.load(open(R("k6", "summary.json")))["metrics"] if os.path.exists(R("k6", "summary.json")) else {}
pgbench = re.search(r"tps = ([\d.]+)", open(R("pgbench.txt")).read())
dbsize = lambda path: dict(re.findall(r"== (\S+)\n(?:\(integer\) )?(\S+)", open(path).read())) if os.path.exists(path) else {}

miss_window = window["miss"]
out = {
    "cond": cond,
    "redis": head["redis"],
    "op": head["op"],
    "cpu_model": (re.search(r"Model name:\s+(.*)", open(R("00-environment.txt")).read()) or [None, None])[1],
    "vendor": (re.search(r"Vendor ID:\s+(.*)", open(R("00-environment.txt")).read()) or [None, None])[1],
    "pgbench_tps": float(pgbench.group(1)) if pgbench else None,
    "observed_seconds": stop - op,
    "stop_reason": stop_reason,
    "stopped": stopped,
    "stopped_per_pod": [round(x, 2) for x in sorted(per_pod)],
    "probe_blips_rel": short_spans,
    "first_request_error": rel(first_request_error),
    "last_request_error": rel(last_request_error),
    "first_errors": first_errors,
    "steps": steps,
    "load_seconds": load_seconds,
    "sentinel": sentinel,
    "req": window["req"],
    "miss": miss_window,
    "miss_redis_failed": window["redis_failed"],
    "miss_redis_absent": window["redis_absent"],
    "local_hit": window["local_hit"],
    "redis_hit": window["redis_hit"],
    "redis_set_ok": window["redis_set_ok"],
    "db_reads": window["db_start"],
    "db_done": window["db_done"],
    "db_err": window["db_err"],
    "dup": window["dup"],
    "app_errors": window["errs"],
    "get_err": {e: window["get_err_" + e] for e in ERR},
    "set_err": {e: window["set_err_" + e] for e in ERR},
    "other_messages": dict(other_msgs),
    "p99_baseline_ms": pct(base_lat, 0.99),
    "p99_ms": pct(all_lat, 0.99),
    "p99_excluding_hot_ms": pct(cool_lat, 0.99),
    "p99_peak_second_ms": max((pct(lat[s], 0.99) for s in win_secs if s in lat), default=None),
    "requests_in_window": len(all_lat),
    "bad_status": sum(bad[s] for s in win_secs),
    "node_cpu_95_seconds": len(hot),
    "node_cpu_95_rel": [s - op_s for s in hot],
    "node_cpu_baseline": sum(cpu[s]["node"] for s in base_secs if s in cpu) / max(1, len([s for s in base_secs if s in cpu])),
    "db_cpu_peak": max((cpu[s]["pg"] for s in win_secs if s in cpu), default=None),
    "stall_longest_seconds": stall_longest,
    "requests_over_100ms": slow,
    "k6_dropped": k6.get("dropped_iterations", {}).get("values", {}).get("count", 0),
    "k6_vus_max": k6.get("vus_max", {}).get("values", {}).get("max"),
    "k6_iterations": k6.get("iterations", {}).get("values", {}).get("count"),
    "dbsize_before": dbsize(R("redis-before.txt")),
    "dbsize_after": dbsize(R("redis-after.txt")),
    "elapsed": open(R("elapsed.txt")).read().split("\n"),
}

problems = []
if "k6_incomplete" in timeline:
    problems.append("k6 did not finish (%s)" % next(x.split()[2] for x in open(R("timeline.txt")) if x.startswith("k6_incomplete")))
if redis_pod:
    sizes = set(out["dbsize_before"].values())
    if head["redis"] == "sentinel" and sizes != {params["KEYS"]}:
        problems.append("Redis held %s keys before the operation, expected %s on every server" % (sizes, params["KEYS"]))
    if head["redis"] != "sentinel" and sizes != {params["KEYS"]}:
        problems.append("Redis held %s keys before the operation, expected %s" % (sizes, params["KEYS"]))
out["problems"] = problems
json.dump(out, open(R("metrics.json"), "w"), indent=1)
for p in problems:
    print("RUN INVALID: " + p)
print("# %s\n" % cond)
print("```json\n%s\n```" % json.dumps({k: v for k, v in out.items() if k != "elapsed"}, indent=1))
