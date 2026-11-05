import json
import os
import sys

K6_TIMEOUT_MS = 30000
STOPPED = "seconds Redis could not be read"
FAILED = "requests sent to the DB because the Redis read failed"
ABSENT = "requests sent to the DB because the key was not in Redis"
TOTAL = "requests sent to the DB"
P99 = "p99 (ms)"
EVENTS = {
    "sentinel-restart": (
        ("SIGTERM", "sigterm", "The master received SIGTERM from the rollout"),
        ("RDB save start", "save_start", "The master started saving before exiting and stopped answering"),
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
    "+switch-master": "Sentinel announced the new master",
}


def f(v, fmt="%.1f"):
    return "-" if v is None else fmt % v


def i(v):
    return "-" if v is None else "%d" % v


def diff(a, b):
    return None if a is None or b is None else b - a


def table(header, rows):
    print("| %s |" % " | ".join(header))
    print("|%s" % ("---|" * len(header)))
    for row in rows:
        print("| %s |" % " | ".join(row))
    print()


def period(m):
    return "from the operation until measuring stopped, %.1f s after it" % m["observed_seconds"]


def stop_note(m, p99=False):
    note = ("Counted %s. Seconds Redis could not be read run from the last Redis read that succeeded to the first that "
            "succeeded again, across the 4 app Pods." % period(m))
    if p99:
        note += " p99 is the 99th percentile of the request latency measured by k6 for the requests that completed in the same period."
        if m["p99_ms"] is not None and m["p99_ms"] >= K6_TIMEOUT_MS:
            note += " k6 gives up on a request after 30 s, so a p99 of %d ms is cut off at that limit." % K6_TIMEOUT_MS
    print(note + "\n")


def local_only(m):
    print("DB reads are the queries the app sent to PostgreSQL in one second. The first row is the mean of the 10 "
          "seconds before the operation. The other rows are single seconds.\n")
    peak = m["db_reads_peak"]
    later = m["db_later"]
    table(["seconds after the operation", "DB reads per second"], [
        ["mean of the 10 s before the operation", f(m["db_reads_before"])],
        ["%d (the second with the most reads)" % peak["second"], i(peak["reads"])],
        ["%d" % later["second"], i(later["reads"])],
    ])
    print("Totals %s.\n" % period(m))
    table(["requests in the %.1f s after the operation" % m["observed_seconds"], "of those, %s" % TOTAL], [[i(m["req"]), i(m["miss"])]])


def app_restart(m):
    print("Totals %s.\n" % period(m))
    table(["requests", TOTAL], [[i(m["req"]), i(m["miss"])]])


def short_timeouts(m):
    stop_note(m, p99=True)
    table([STOPPED, TOTAL, P99], [[f(m["stopped_seconds"]), i(m["miss"]), f(m["p99_ms"])]])


def no_persistence(m):
    stop_note(m, p99=True)
    print("The last two columns count the Redis commands that failed with that error after go-redis finished its retries, "
          "in the same period.\n")
    table([STOPPED, FAILED, ABSENT, TOTAL, P99, "Redis commands failed with connection refused", "Redis commands failed with timeout"],
          [[f(m["stopped_seconds"]), i(m["miss_redis_failed"]), i(m["miss_redis_absent"]), i(m["miss"]), f(m["p99_ms"]),
            i(m["refused"]), i(m["timeout"])]])
    t = m["steps"]
    print("Each step runs from one point to the next, from the operation until the last app Pod read Redis again.\n")
    table(["step", "from", "to", "seconds"], [
        ["termination", "the operation", "the old Redis process exited", f(t.get("exit"), "%.2f")],
        ["Pod start", "the old Redis process exited", "the new Redis process started",
         f(diff(t.get("exit"), t.get("process_start")), "%.2f")],
        ["load", "the new Redis process started", "Redis accepted connections",
         f(diff(t.get("process_start"), t.get("accepting")), "%.2f")],
        ["probe and back in Service", "Redis accepted connections", "the last app Pod read Redis again",
         f(diff(t.get("accepting"), t.get("last_pod_back")), "%.2f")],
    ])


def rdb(m):
    stop_note(m)
    table([STOPPED, FAILED, ABSENT, "seconds from the operation until the old Redis process exited"],
          [[f(m["stopped_seconds"]), i(m["miss_redis_failed"]), i(m["miss_redis_absent"]), f(m["steps"].get("exit"), "%.2f")]])


def events_table(m):
    t = m["steps"]
    events = [(t[key], name, text) for name, key, text in EVENTS[m["cond"]] if t.get(key) is not None]
    events += [(s, name, SENTINEL_EVENTS[name]) for name, s in m["sentinel"].items()]
    table(["seconds after the operation", "event", "what happened"],
          [["%.2f" % s, name, text] for s, name, text in sorted(events)])


def sentinel_restart(m):
    print("### sentinel-restart\n")
    stop_note(m)
    table([STOPPED, ABSENT], [[f(m["stopped_seconds"]), i(m["miss_redis_absent"])]])
    print("`sentinel-restart`, what happened after the operation. A Sentinel event is the first time any of the 3 Sentinels "
          "logged it. Redis reads stop at `RDB save start`, so that row is the starting point of this table. SIGTERM comes late "
          "in some runs, so the seconds of that row differ from run to run.\n")
    events_table(m)


def sentinel_kill(m):
    print("### sentinel-kill\n")
    stop_note(m)
    table([STOPPED, ABSENT], [[f(m["stopped_seconds"]), i(m["miss_redis_absent"])]])
    print("`sentinel-kill`, what happened after the operation.\n")
    events_table(m)
    print("`sentinel-kill`, %s: the 3 Sentinels logged `+switch-master` %d times, and Redis refused %d writes with "
          "`NOREPLICAS Not enough good replicas to write`.\n" % (period(m), m["switch_master_lines"], m["noreplicas"]))
    print("`sentinel-kill`, each replica after the master came back: when its log shows `Flushing old data`, and how many "
          "keys its log shows after that in `Done loading RDB, keys loaded`, for the RDB received from the master.\n")
    table(["replica", "seconds after the operation", "keys loaded"],
          [[p, f(r["flushed"], "%.2f"), i(r["keys_loaded"])] for p, r in sorted(m["replicas"].items())])


def cached_local(m):
    stop_note(m)
    table([STOPPED, FAILED], [[f(m["stopped_seconds"]), i(m["miss_redis_failed"])]])


def sentinel(rows):
    for m in rows:
        (sentinel_restart if m["cond"] == "sentinel-restart" else sentinel_kill)(m)


def each(render):
    def run(rows):
        for m in rows:
            render(m)
    return run


SECTIONS = [
    ("2", "Cache: a local cache in each app Pod, no Redis. Operation: `kubectl rollout restart` of the 4 app Pods.", ["app-restart-local"], each(local_only)),
    ("3-1", "Cache: Redis only, a single instance with a PVC. Operation: `kubectl rollout restart` of the 4 app Pods.", ["app-restart-redis"], each(app_restart)),
    ("4-1", "Cache: Redis only, a single instance with `save \"\"` and no PVC. Operation: `kubectl rollout restart` of Redis.", ["redis-restart-none"], each(no_persistence)),
    ("4-2", "Compared with 4-1, only the go-redis settings change: DialTimeout, ReadTimeout, WriteTimeout and PoolTimeout of 500 ms, DialerRetries 1 and MaxRetries 1. Cache: Redis only, a single instance with `save \"\"` and no PVC. Operation: `kubectl rollout restart` of Redis.", ["redis-restart-none-short"], each(short_timeouts)),
    ("4-3", "Cache: Redis only, a single instance with a PVC and `save 900 1`. Operation: `kubectl rollout restart` of Redis, which sends it SIGTERM.", ["redis-restart-rdb"], each(rdb)),
    ("4-4", "Cache: Redis only, the redis-ha chart with Sentinel and `down-after-milliseconds` 5000. Operation of `sentinel-restart`: `kubectl rollout restart` with a partition of 2, which replaces only the master, `redis-ha-server-2`. Operation of `sentinel-kill`: SIGKILL to the master, `redis-ha-server-0`.", ["sentinel-restart", "sentinel-kill"], sentinel),
    ("4-5", "Cache: a local cache of 120 entries in front of Redis, a single instance with `save \"\"` and no PVC. Operation: `kubectl rollout restart` of Redis.", ["redis-restart-none-local"], each(cached_local)),
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
        print("## %s\n\nJob: %s. %s\n" % (number, ", ".join("`%s (%s)`" % (number, m["cond"]) for m in rows), condition))
        render(rows)
