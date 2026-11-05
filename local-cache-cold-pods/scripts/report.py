import json
import os
import sys

DEFINITIONS = (
    "Requests are counted from the operation until the job stopped. "
    "A miss is a request that the caches did not answer and that went to the DB. "
    "Misses during the stop are those whose Redis read failed, refill misses those whose key was not in Redis. "
    "Seconds stopped runs from the last Redis read that succeeded to the first that succeeded again, across the 4 app Pods. "
    "connection refused, timeout and NOREPLICAS count the Redis commands that failed after go-redis finished its retries. "
    "p99 is from k6 by completion, for all requests from the operation until the job stopped. "
    "p99 before is for the 10 seconds before the operation. "
    "DB reads are the queries sent to PostgreSQL. "
    "DB CPU is the CPU usage of the PostgreSQL container as a percentage of its CPU set."
)
STOP_COLUMNS = ["seconds stopped", "misses during the stop", "refill misses", "p99 (ms)"]


def f(v, fmt="%.1f"):
    return "-" if v is None else fmt % v


def i(v):
    return "-" if v is None else "%d" % v


def diff(a, b):
    return None if a is None or b is None else b - a


def job(number, m):
    return "%s (%s)" % (number, m["cond"])


def table(header, rows):
    print("| %s |" % " | ".join(header))
    print("|%s" % ("---|" * len(header)))
    for row in rows:
        print("| %s |" % " | ".join(row))
    print()


def stop_values(m):
    return [f(m["stopped_seconds"]), i(m["miss_redis_failed"]), i(m["miss_redis_absent"]), f(m["p99_ms"])]


def local_only(number, rows):
    for m in rows:
        print("%s. DB reads and DB CPU, by second from the operation.\n" % job(number, m))
        peak = m["db_reads_peak"]
        later = m["db_later"]
        table(["second", "DB reads per second", "DB CPU (%)"], [
            ["mean of the 10 s before", f(m["db_reads_before"]), f(m["db_cpu_before"])],
            ["%d, most reads" % peak["second"], i(peak["reads"]), f(peak["cpu"])],
            ["%d" % later["second"], i(later["reads"]), f(later["cpu"])],
        ])
    table(["job", "peak DB CPU (%)", "DB reads until the job stopped", "fewest misses of a new Pod", "most misses of a new Pod", "p99 (ms)", "p99 before (ms)"],
          [[job(number, m), f(m["db_cpu_peak"]), i(m["db_reads"]), i(m["new_pod_miss_min"]), i(m["new_pod_miss_max"]),
            f(m["p99_ms"]), f(m["p99_baseline_ms"])] for m in rows])


def app_restart(number, rows):
    table(["job", "requests", "misses"], [[job(number, m), i(m["req"]), i(m["miss"])] for m in rows])


def stop_only(number, rows):
    table(["job"] + STOP_COLUMNS, [[job(number, m)] + stop_values(m) for m in rows])


def no_persistence(number, rows):
    table(["job"] + STOP_COLUMNS + ["connection refused", "timeout"],
          [[job(number, m)] + stop_values(m) + [i(m["refused"]), i(m["timeout"])] for m in rows])
    print("Seconds of each step, from the operation until the last app Pod read Redis again.\n")
    table(["job", "termination", "Pod start", "load", "probe", "back in Service"], [[
        job(number, m), f(t.get("exit"), "%.2f"), f(diff(t.get("exit"), t.get("process_start")), "%.2f"),
        f(diff(t.get("process_start"), t.get("accepting")), "%.2f"), f(diff(t.get("accepting"), t.get("pod_ready")), "%.2f"),
        f(diff(t.get("pod_ready"), t.get("last_pod_back")), "%.2f")] for m in rows for t in [m["steps"]]])


def rdb(number, rows):
    table(["job"] + STOP_COLUMNS + ["termination (s)", "`DB loaded from disk` (s)"],
          [[job(number, m)] + stop_values(m) + [f(m["steps"].get("exit"), "%.2f"), f(m["load_seconds"], "%.3f")] for m in rows])


def sentinel(number, rows):
    stop_only(number, rows)
    for m in rows:
        print("%s. Seconds from the operation.\n" % job(number, m))
        t = m["steps"]
        names = (("SIGTERM on the master", "sigterm"), ("old master exit", "exit"), ("last app Pod reads again", "last_pod_back"))
        if m["cond"] == "sentinel-kill":
            names = (("old master process start", "process_start"), ("last app Pod reads again", "last_pod_back"))
        events = [(t[key], name) for name, key in names if t.get(key) is not None]
        events += [(s, name) for name, s in m["sentinel"].items()]
        table(["event", "s"], [[name, "%.2f" % s] for s, name in sorted(events)])
        print("%s. Keys per server.\n" % job(number, m))
        table(["server", "before", "after"],
              [[p, m["dbsize_before"].get(p, "-"), m["dbsize_after"].get(p, "-")] for p in sorted(m["dbsize_before"])])
        if m["cond"] == "sentinel-kill":
            print("%s. NOREPLICAS errors: %d.\n" % (job(number, m), m["noreplicas"]))


SECTIONS = [
    ("2", "Local cache only. `kubectl rollout restart` of the 4 app Pods.", ["app-restart-local"], local_only),
    ("3-1", "Redis only, single instance with PVC. `kubectl rollout restart` of the 4 app Pods.", ["app-restart-redis"], app_restart),
    ("3-3", "Redis only, single instance, `save \"\"` and no PVC, go-redis with DialTimeout, ReadTimeout, WriteTimeout and PoolTimeout 500 ms, DialerRetries 1 and MaxRetries 1. `kubectl rollout restart` of Redis.", ["redis-restart-none-short"], stop_only),
    ("4-1", "Redis only, single instance, `save \"\"` and no PVC. `kubectl rollout restart` of Redis.", ["redis-restart-none"], no_persistence),
    ("4-2", "Redis only, single instance, PVC and `save 900 1`. `kubectl rollout restart` of Redis (SIGTERM).", ["redis-restart-rdb"], rdb),
    ("4-3", "Redis only, redis-ha chart, Sentinel with `down-after-milliseconds` 5000. `sentinel-restart` replaces only the master, `redis-ha-server-2`, with `kubectl rollout restart` and a partition of 2. `sentinel-kill` sends SIGKILL to the master, `redis-ha-server-0`.", ["sentinel-restart", "sentinel-kill"], sentinel),
    ("4-4", "Single instance, `save \"\"` and no PVC, with a local cache of 120 entries in front of Redis. `kubectl rollout restart` of Redis.", ["redis-restart-none-local"], stop_only),
]

metrics = {}
for d in sys.argv[1:]:
    p = os.path.join(d, "metrics.json")
    if os.path.exists(p):
        m = json.load(open(p))
        metrics[m["cond"]] = m
    else:
        s = os.path.join(d, "summary.md")
        print("%s: no metrics.json. %s\n" % (os.path.basename(d.rstrip("/")), open(s).read().strip() if os.path.exists(s) else ""))

print("# local-cache-cold-pods\n")
for m in metrics.values():
    for problem in m["problems"]:
        print("RUN INVALID (%s): %s\n" % (m["cond"], problem))
if metrics:
    print(DEFINITIONS + "\n")
for number, condition, conds, render in SECTIONS:
    rows = [metrics[c] for c in conds if c in metrics]
    if rows:
        print("## %s\n\n%s\n" % (number, condition))
        render(number, rows)
