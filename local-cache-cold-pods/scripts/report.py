import json
import os
import sys

runs = {}
elapsed = {}
dirs = {}
for d in sys.argv[1:]:
    p = os.path.join(d, "metrics.json")
    if not os.path.exists(p):
        continue
    m = json.load(open(p))
    runs[m["cond"]] = m
    dirs[m["cond"]] = d
    el = {}
    for line in open(os.path.join(d, "elapsed.txt")):
        k, v = line.split()
        el[k] = v
    elapsed[m["cond"]] = el

if not runs:
    print("RUN INVALID: no metrics.json in %s" % " ".join(sys.argv[1:]))
    sys.exit(0)

SECTIONS = {"restart-default": "3, 4", "restart-surge1": "4"}


def job(cond):
    return "%s (%s)" % (SECTIONS.get(cond, "3"), cond)


adds = sorted((c for c in runs if c.startswith("add-")), key=lambda c: int(c[4:]))
order = adds + [c for c in ("restart-default", "restart-surge1") if c in runs]


def restart_table(conds):
    print("| job | strategy | peak DB reads/s | rollout (s) |")
    print("|---|---|---|---|")
    for c in conds:
        m = runs[c]
        st = "maxSurge 1, maxUnavailable 0, minReadySeconds %s" % m["params"]["MIN_READY"] if c == "restart-surge1" else \
            "default (maxSurge 25%, maxUnavailable 25%, minReadySeconds 0)"
        print("| %s | %s | %.0f | %.1f |" % (job(c), st, m["peak_db"]["db_per_s"], m["rollout_s"]))
    print()


def per_second(c):
    m = runs[c]
    print("%s, per second from 2 s before the operation until 3 s after DB reads fell below 10/s. DB CPU is the share of the CPUs the PostgreSQL container runs on (%s). pool wait is the mean wait of the pgxpool acquires that found no idle connection. p99 is from k6." % (job(c), m["params"]["PG_CPUS"]))
    print()
    print("| s | DB reads/s | DB CPU % | pool wait ms | p99 ms |")
    print("|---|---|---|---|---|")
    rows = [line.rstrip("\n").split("\t") for line in open(os.path.join(dirs[c], "timeseries.tsv"))]
    cols = rows[0]
    for r in rows[1:]:
        r = dict(zip(cols, r))
        rel = int(r["rel"])
        if -2 <= rel <= m["window_end_s"]:
            print("| %+d | %.0f | %s | %.0f | %.1f |" % (
                rel, float(r["db"]), "-" if r["pg_cpu"] == "" else "%.0f" % float(r["pg_cpu"]),
                float(r["pool_wait_ms"]), float(r["p99"])))
    print()


print("# local-cache-cold-pods")
print()
bad = [(c, msg) for c in order for msg in runs[c]["invalid"]]
for c, msg in bad:
    print("RUN INVALID: %s: %s" % (job(c), msg))
if bad:
    print()

print("## 3. Reproduce")
print()
print("Pods added to 4 Pods with filled caches. DB reads are counted by the app when a request misses the local cache, summed over all Pods per second.")
print()
print("| job | Pods added | peak DB reads/s | peak / all requests | DB reads by the added Pods |")
print("|---|---|---|---|---|")
for c in adds:
    m = runs[c]
    pk = m["peak_db"]
    print("| %s | %d | %.0f | %.2f | %d |" % (job(c), m["add"], pk["db_per_s"], pk["ratio"], m["new_db_total"]))
print()
if "restart-default" in runs:
    print("`kubectl rollout restart` of the 4 Pods. Rollout time is from the command until `kubectl rollout status` returned.")
    print()
    restart_table(["restart-default"])
full = next((c for c in ("restart-default", "add-4") if c in runs and runs[c]["pg_cpu_ge_90_s"]), None)
if full:
    per_second(full)

print("## 4. Mitigations")
print()
rs = [c for c in ("restart-default", "restart-surge1") if c in runs]
if rs:
    print("`kubectl rollout restart` of the 4 Pods with each rolling update strategy.")
    print()
    restart_table(rs)
