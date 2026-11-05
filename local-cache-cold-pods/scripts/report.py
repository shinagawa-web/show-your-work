import json
import os
import sys

K6_TIMEOUT_MS = 30000
RESTARTED = {"sentinel-restart": "redis-ha-server-2", "sentinel-kill": "redis-ha-server-0"}
STOP_COLUMNS = ["seconds Redis could not be read", "requests sent to the DB because the Redis read failed",
                "requests sent to the DB because the key was not in Redis", "p99 (ms)"]
EVENTS = {
    "sentinel-restart": (
        ("SIGTERM", "sigterm", "The master received SIGTERM from the rollout"),
        ("old master exit", "exit", "The Redis process of the old master exited"),
        ("last app Pod reads again", "last_pod_back", "The last of the 4 app Pods read Redis successfully again"),
    ),
    "sentinel-kill": (
        ("old master process start", "process_start", "The killed Redis process started again"),
        ("last app Pod reads again", "last_pod_back", "The last of the 4 app Pods read Redis successfully again"),
    ),
}
SENTINEL_EVENTS = {
    "+sdown": "Sentinel marked the master as unreachable",
    "+try-failover": "Sentinel started a failover",
    "+switch-master": "Sentinel announced the new master",
}


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


def job_table(number, header, rows, values):
    if len(rows) == 1:
        table(header, [values(rows[0])])
    else:
        table(["job"] + header, [[job(number, m)] + values(m) for m in rows])


def period(rows):
    if len(rows) == 1:
        return "from the operation until measuring stopped, %.1f s after it" % rows[0]["observed_seconds"]
    return "from the operation until measuring stopped, %s" % " and ".join(
        "%.1f s after it in `%s`" % (m["observed_seconds"], m["cond"]) for m in rows)


def stop_note(rows):
    note = ("Counted %s. Seconds Redis could not be read run from the last Redis read that succeeded to the first that "
            "succeeded again, across the 4 app Pods. p99 is the 99th percentile of the request latency measured by k6 for "
            "the requests that completed in the same period." % period(rows))
    if any(m["p99_ms"] is not None and m["p99_ms"] >= K6_TIMEOUT_MS for m in rows):
        note += " k6 gives up on a request after 30 s, so a p99 of %d ms is cut off at that limit." % K6_TIMEOUT_MS
    print(note + "\n")


def stop_values(m):
    return [f(m["stopped_seconds"]), i(m["miss_redis_failed"]), i(m["miss_redis_absent"]), f(m["p99_ms"])]


def local_only(number, rows):
    for m in rows:
        print("DB reads are the queries the app sent to PostgreSQL in one second. The first row is the mean of the 10 "
              "seconds before the operation. The other rows are single seconds.\n")
        peak = m["db_reads_peak"]
        later = m["db_later"]
        table(["seconds after the operation", "DB reads per second"], [
            ["mean of the 10 s before the operation", f(m["db_reads_before"])],
            ["%d (the second with the most reads)" % peak["second"], i(peak["reads"])],
            ["%d" % later["second"], i(later["reads"])],
        ])
        print("Totals %s. A new Pod is one of the 4 Pods the rollout started, with an empty local cache.\n" % period([m]))
        table(["DB reads in the %.1f s after the operation, all Pods" % m["observed_seconds"],
               "DB reads by the new Pod that sent the fewest", "DB reads by the new Pod that sent the most"],
              [[i(m["db_reads"]), i(m["new_pod_miss_min"]), i(m["new_pod_miss_max"])]])
        print("p99 of the request latency measured by k6, for the requests that completed in each period.\n")
        table(["period", "p99 (ms)"], [
            ["the 10 s before the operation", f(m["p99_baseline_ms"])],
            [period([m]), f(m["p99_ms"])],
        ])


def app_restart(number, rows):
    print("Totals %s.\n" % period(rows))
    job_table(number, ["requests", "requests sent to the DB"], rows, lambda m: [i(m["req"]), i(m["miss"])])


def stop_only(number, rows):
    stop_note(rows)
    job_table(number, STOP_COLUMNS, rows, stop_values)


def no_persistence(number, rows):
    stop_note(rows)
    print("The last two columns count the Redis commands that failed with that error after go-redis finished its retries, "
          "in the same period.\n")
    job_table(number, STOP_COLUMNS + ["Redis commands failed with connection refused", "Redis commands failed with timeout"],
              rows, lambda m: stop_values(m) + [i(m["refused"]), i(m["timeout"])])
    for m in rows:
        t = m["steps"]
        print("Each step runs from one point to the next, from the operation until the last app Pod read Redis again.\n")
        table(["step", "from", "to", "seconds"], [
            ["termination", "the operation", "the old Redis process exited", f(t.get("exit"), "%.2f")],
            ["Pod start", "the old Redis process exited", "the new Redis process started",
             f(diff(t.get("exit"), t.get("process_start")), "%.2f")],
            ["load", "the new Redis process started", "Redis accepted connections",
             f(diff(t.get("process_start"), t.get("accepting")), "%.2f")],
            ["probe", "Redis accepted connections", "the readiness probe passed and the Pod was Ready",
             f(diff(t.get("accepting"), t.get("pod_ready")), "%.2f")],
            ["back in Service", "the Pod was Ready", "the last app Pod read Redis again",
             f(diff(t.get("pod_ready"), t.get("last_pod_back")), "%.2f")],
        ])


def rdb(number, rows):
    stop_note(rows)
    job_table(number, STOP_COLUMNS, rows, stop_values)
    for m in rows:
        print("Two steps inside the time Redis could not be read.\n")
        table(["step", "what it measures", "seconds"], [
            ["termination", "from the operation until the old Redis process exited", f(m["steps"].get("exit"), "%.2f")],
            ["`DB loaded from disk`", "the load time Redis wrote in this log line", f(m["load_seconds"], "%.3f")],
        ])


def sentinel(number, rows):
    stop_note(rows)
    job_table(number, STOP_COLUMNS, rows, stop_values)
    for m in rows:
        t = m["steps"]
        note = " A Sentinel event is the first time any of the 3 Sentinels logged it." if m["sentinel"] else ""
        print("`%s`, what happened after the operation.%s\n" % (m["cond"], note))
        events = [(t[key], name, text) for name, key, text in EVENTS[m["cond"]] if t.get(key) is not None]
        events += [(s, name, SENTINEL_EVENTS[name]) for name, s in m["sentinel"].items()]
        table(["seconds after the operation", "event", "what happened"],
              [["%.2f" % s, name, text] for s, name, text in sorted(events)])
        print("`%s`, keys held by each Redis server, read just before the operation and after the load stopped.\n" % m["cond"])
        after = lambda p: m["dbsize_after"].get(p, "-")
        table(["server", "keys before the operation", "keys after the load stopped"],
              [[p, m["dbsize_before"].get(p, "-"),
                "unavailable (restarting)" if p == RESTARTED[m["cond"]] and not after(p).isdigit() else after(p)]
               for p in sorted(m["dbsize_before"])])
        if m["cond"] == "sentinel-kill":
            print("`sentinel-kill`, %s: Redis refused %d writes with `NOREPLICAS Not enough good replicas to write`.\n"
                  % (period([m]), m["noreplicas"]))


SECTIONS = [
    ("2", "Cache: a local cache in each app Pod, no Redis. Operation: `kubectl rollout restart` of the 4 app Pods.", ["app-restart-local"], local_only),
    ("3-1", "Cache: Redis only, a single instance with a PVC. Operation: `kubectl rollout restart` of the 4 app Pods.", ["app-restart-redis"], app_restart),
    ("3-3", "Cache: Redis only, a single instance with `save \"\"` and no PVC. go-redis uses DialTimeout, ReadTimeout, WriteTimeout and PoolTimeout of 500 ms, DialerRetries 1 and MaxRetries 1. Operation: `kubectl rollout restart` of Redis.", ["redis-restart-none-short"], stop_only),
    ("4-1", "Cache: Redis only, a single instance with `save \"\"` and no PVC. Operation: `kubectl rollout restart` of Redis.", ["redis-restart-none"], no_persistence),
    ("4-2", "Cache: Redis only, a single instance with a PVC and `save 900 1`. Operation: `kubectl rollout restart` of Redis, which sends it SIGTERM.", ["redis-restart-rdb"], rdb),
    ("4-3", "Cache: Redis only, the redis-ha chart with Sentinel and `down-after-milliseconds` 5000. Operation of `sentinel-restart`: `kubectl rollout restart` with a partition of 2, which replaces only the master, `redis-ha-server-2`. Operation of `sentinel-kill`: SIGKILL to the master, `redis-ha-server-0`.", ["sentinel-restart", "sentinel-kill"], sentinel),
    ("4-4", "Cache: a local cache of 120 entries in front of Redis, a single instance with `save \"\"` and no PVC. Operation: `kubectl rollout restart` of Redis.", ["redis-restart-none-local"], stop_only),
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
    print("In every job k6 sends a constant %d requests per second to the app. The operation of each section runs during that load.\n"
          % next(iter(metrics.values()))["rate"])
for number, condition, conds, render in SECTIONS:
    rows = [metrics[c] for c in conds if c in metrics]
    if rows:
        print("## %s\n\nJob: %s. %s\n" % (number, ", ".join("`%s`" % job(number, m) for m in rows), condition))
        render(number, rows)
