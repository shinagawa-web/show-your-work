import json
import os
import sys

SECTIONS = [
    ("2", "Local cache only. `kubectl rollout restart` of the 4 app Pods.", ["app-restart-local"]),
    ("3-1", "Redis only, single instance with PVC. `kubectl rollout restart` of the 4 app Pods.", ["app-restart-redis"]),
    ("3-3", "Redis only, single instance, `save \"\"` and no PVC, go-redis with DialTimeout, ReadTimeout, WriteTimeout and PoolTimeout 500 ms, DialerRetries 1 and MaxRetries 1. `kubectl rollout restart` of Redis.", ["redis-restart-none-short"]),
    ("4-1", "Redis only, single instance, `save \"\"` and no PVC. `kubectl rollout restart` of Redis.", ["redis-restart-none"]),
    ("4-2", "Redis only, single instance, PVC and `save 900 1`. `kubectl rollout restart` of Redis (SIGTERM).", ["redis-restart-rdb"]),
    ("4-3", "Redis only, redis-ha chart, Sentinel with `down-after-milliseconds` 5000. `sentinel-restart` replaces only the master, `redis-ha-server-2`, with `kubectl rollout restart` and a partition of 2. `sentinel-kill` sends SIGKILL to the master, `redis-ha-server-0`.", ["sentinel-restart", "sentinel-kill"]),
    ("4-4", "Single instance, `save \"\"` and no PVC, with a local cache of 120 entries in front of Redis. `kubectl rollout restart` of Redis.", ["redis-restart-none-local"]),
]


def f(v, fmt="%.1f"):
    return "-" if v is None else fmt % v


def i(v):
    return "-" if v is None else "%d" % v


def diff(a, b):
    return None if a is None or b is None else b - a


metrics = {}
for d in sys.argv[1:]:
    p = os.path.join(d, "metrics.json")
    if os.path.exists(p):
        m = json.load(open(p))
        metrics[m["cond"]] = m
    else:
        s = os.path.join(d, "summary.md")
        print("%s: no metrics.json. %s\n" % (os.path.basename(d.rstrip("/")), open(s).read().strip() if os.path.exists(s) else ""))

first = next(iter(metrics.values()), None)
print("# local-cache-cold-pods\n")
if first:
    print("Requests are counted from the operation until the job stopped. A miss is a request that the caches did not answer and that went to the DB. Seconds stopped runs from the last Redis read that succeeded to the first that succeeded again, across the 4 app Pods. Errors are the Redis commands that failed after go-redis finished its retries. p99 is from k6 by completion, for all requests from the operation until the job stopped.\n")
for number, condition, conds in SECTIONS:
    rows = [metrics[c] for c in conds if c in metrics]
    if not rows:
        continue
    print("## %s\n\n%s\n" % (number, condition))
    print("| job | requests | misses | during the stop | refill | seconds stopped | LOADING | connection refused | timeout | READONLY | other errors | p99 (ms) | p99 before (ms) | seconds with node CPU >= 95% |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for m in rows:
        g = m["get_err"]
        s = m["set_err"]
        total = {k: g[k] + s[k] for k in g}
        st = m["stopped"]
        print("| %s (%s) | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %d |" % (
            number, m["cond"], i(m["req"]), i(m["miss"]), i(m["miss_redis_failed"]), i(m["miss_redis_absent"]),
            f(st["seconds"]) if st else "0", i(total["loading"]), i(total["refused"]), i(total["timeout"]),
            i(total["readonly"]), i(total["other"]), f(m["p99_ms"]), f(m["p99_baseline_ms"]), m["node_cpu_95_seconds"]))
    print()
    st_rows = [m for m in rows if m["steps"] and m["redis"].startswith("single")]
    if st_rows:
        print("Seconds stopped, measured from the operation.\n")
        print("| job | termination | Pod start | load | probe | back in Service | total | `DB loaded from disk` |")
        print("|---|---|---|---|---|---|---|---|")
        for m in st_rows:
            t = m["steps"]
            print("| %s (%s) | %s | %s | %s | %s | %s | %s | %s |" % (
                number, m["cond"], f(t.get("exit"), "%.2f"), f(diff(t.get("exit"), t.get("process_start")), "%.2f"),
                f(diff(t.get("process_start"), t.get("accepting")), "%.2f"), f(diff(t.get("accepting"), t.get("pod_ready")), "%.2f"),
                f(diff(t.get("pod_ready"), t.get("last_pod_back")), "%.2f"), f(t.get("last_pod_back"), "%.2f"),
                f(m["load_seconds"], "%.3f")))
        print()
    for m in rows:
        if m["redis"] == "sentinel":
            print("%s (%s). Seconds from the operation.\n" % (number, m["cond"]))
            print("| event | s |")
            print("|---|---|")
            t = m["steps"]
            for name, key in (("SIGTERM on the master", "sigterm"), ("old master exit", "exit"), ("old master process start", "process_start"),
                              ("old master accepting", "accepting"), ("old master Ready", "pod_ready"),
                              ("first app Pod reads again", "first_pod_back"), ("last app Pod reads again", "last_pod_back")):
                if t.get(key) is not None:
                    print("| %s | %.2f |" % (name, t[key]))
            seen = set()
            for e in m["sentinel"]:
                if ("switch-master" in e["msg"] or "+odown" in e["msg"] or "+sdown master" in e["msg"]) and e["msg"][:12] not in seen:
                    seen.add(e["msg"][:12])
                    print("| Sentinel `%s` | %.2f |" % (e["msg"].split()[0], e["t"]))
            print()
            print("Keys per server before: %s. After: %s.\n" % (m["dbsize_before"], m["dbsize_after"]))

if metrics:
    print("## 数字は使わない\n")
    print("| job | CPU | pgbench tps | DB reads | peak s p99 (ms) | p99 without hot seconds (ms) | longest stall (s) | requests over 100 ms | k6 dropped | k6 VUs max | observed (s) | stopped by | job total |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for m in metrics.values():
        total = next((x.split()[1] for x in m["elapsed"] if x.startswith("total ")), "-")
        print("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            m["cond"], m["vendor"], f(m["pgbench_tps"], "%.0f"), i(m["db_reads"]), f(m["p99_peak_second_ms"]),
            f(m["p99_excluding_hot_ms"]), f(m["stall_longest_seconds"]), i(m["requests_over_100ms"]), i(m["k6_dropped"]),
            i(m["k6_vus_max"]), f(m["observed_seconds"]), m["stop_reason"], total))
    print()
    print("First Redis error of the probe per job:\n")
    for m in metrics.values():
        if m["first_errors"]:
            print("- %s: %s" % (m["cond"], "; ".join("%s: %s" % (k, e) for k, e in m["first_errors"])))
        if m.get("problems"):
            print("- %s invalid: %s" % (m["cond"], "; ".join(m["problems"])))
        if m["other_messages"]:
            print("- %s other errors: %s" % (m["cond"], json.dumps(m["other_messages"])))
