import json
import os
import sys

STOPPED = "seconds Redis could not be read"
ABSENT = "requests sent to the DB because the key was not in Redis"
TOTAL = "requests sent to the DB"
EXITED = "seconds from the operation until the old Redis process exited"
ACCEPTING = "seconds from the operation until Redis accepted connections"


def f(v, fmt="%.1f"):
    return "-" if v is None else fmt % v


def i(v):
    return "-" if v is None else "%d" % v


def table(header, rows):
    print("| %s |" % " | ".join(header))
    print("|%s" % ("---|" * len(header)))
    for row in rows:
        print("| %s |" % " | ".join(row))
    print()


def period(m):
    return "from the operation until the measurement ended, %.1f s after it" % m["observed_seconds"]


def stop_note(m, verb="Counted"):
    print("%s %s. Seconds Redis could not be read run from the last successful Redis read of the first of the 4 app Pods "
          "to lose Redis until the last of them read Redis successfully again.\n" % (verb, period(m)))


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


def app_restart(m):
    print("Counted %s.\n" % period(m))
    table([TOTAL], [[i(m["miss"])]])


def no_persistence(m):
    stop_note(m, "Measured")
    t = m["steps"]
    table([STOPPED, EXITED, ACCEPTING],
          [[f(m["stopped_seconds"]), f(t.get("exit"), "%.2f"), f(t.get("accepting"), "%.2f")]])


def absent(m):
    stop_note(m)
    table([STOPPED, ABSENT], [[f(m["stopped_seconds"]), i(m["miss_redis_absent"])]])


def rdb(m):
    stop_note(m)
    table([STOPPED, ABSENT, EXITED],
          [[f(m["stopped_seconds"]), i(m["miss_redis_absent"]), f(m["steps"].get("exit"), "%.2f")]])


SECTIONS = [
    ("2", "app-restart-local", "Cache: a local cache in each app Pod, no Redis. Operation: `kubectl rollout restart` of the 4 app Pods.", local_only),
    ("3-1", "app-restart-redis", "Cache: Redis only, a single instance with a PVC. Operation: `kubectl rollout restart` of the 4 app Pods.", app_restart),
    ("4-1", "redis-restart-none", "Cache: Redis only, a single instance with `save \"\"` and no PVC. The readiness and startup probes of Redis use `initialDelaySeconds` 30 and `periodSeconds` 15, the defaults of the redis-ha chart. Operation: `kubectl rollout restart` of Redis.", no_persistence),
    ("4-2", "redis-restart-none-probe", "Cache: Redis only, a single instance with `save \"\"` and no PVC, as in 4-1. The readiness and startup probes of Redis use `initialDelaySeconds` 1 and `periodSeconds` 1. Operation: `kubectl rollout restart` of Redis.", absent),
    ("4-3", "redis-restart-rdb", "Cache: Redis only, a single instance with a PVC and `save 900 1`. The readiness and startup probes of Redis are the same as in 4-1. Operation: `kubectl rollout restart` of Redis, which sends it SIGTERM.", rdb),
    ("4-4", "sentinel-restart", "Cache: Redis only, the redis-ha chart 4.39.0 with the image `public.ecr.aws/docker/library/redis:8.8.0-alpine`, Sentinel and `down-after-milliseconds` 5000. Operation: `kubectl rollout restart` with a partition of 2, which replaces only the master, `redis-ha-server-2`.", absent),
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
for number, cond, condition, render in SECTIONS:
    if cond in metrics:
        print("## %s\n\nJob: `%s (%s)`. %s\n" % (number, number, cond, condition))
        render(metrics[cond])
