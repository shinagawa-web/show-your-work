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

SECTIONS = {"restart-default": "3, 4", "restart-default-ready": "4", "restart-surge1": "4", "restart-surge1-noready": "4"}
RESTARTS = ("restart-default", "restart-surge1-noready", "restart-default-ready", "restart-surge1")


def job(cond):
    return "%s (%s)" % (SECTIONS.get(cond, "3"), cond)


def strategy(c):
    ready = runs[c]["params"]["MIN_READY"]
    return {"restart-default": "default (maxSurge 25%, maxUnavailable 25%, minReadySeconds 0)",
            "restart-surge1-noready": "maxSurge 1, maxUnavailable 0, minReadySeconds 0",
            "restart-default-ready": "default maxSurge and maxUnavailable, minReadySeconds %s" % ready,
            "restart-surge1": "maxSurge 1, maxUnavailable 0, minReadySeconds %s" % ready}[c]


adds = sorted((c for c in runs if c.startswith("add-")), key=lambda c: int(c[4:]))
order = adds + [c for c in RESTARTS if c in runs]


def saturated(c):
    return c in runs and bool(runs[c]["pg_cpu_ge_90_s"])


def per_second(c):
    m = runs[c]
    print("%s, per second from 2 s before the operation until the job recovered. DB CPU is the share of the CPUs the PostgreSQL container runs on (%s). pool wait is the mean wait in that second of the pgxpool acquires that found no idle connection; a wait is added when the acquire succeeds, so acquires canceled while waiting are not included. p99 is from k6, by the second each request completed." % (job(c), m["params"]["PG_CPUS"]))
    print()
    print("| s | DB reads/s | DB CPU % | pool wait ms | p99 ms |")
    print("|---|---|---|---|---|")
    rows = [line.rstrip("\n").split("\t") for line in open(os.path.join(dirs[c], "timeseries.tsv"))]
    cols = rows[0]
    for r in rows[1:]:
        r = dict(zip(cols, r))
        rel = int(r["rel"])
        if -2 <= rel <= m["recovered_s"]:
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
print("Pods added to 4 Pods with filled caches. DB reads are counted by the app when a request misses the local cache, summed over all Pods per second. DB CPU is the share of the CPUs the PostgreSQL container runs on.")
print()
print("| job | CPU model | pgbench tps | Pods added | peak DB reads/s | peak / all requests | DB reads by the added Pods | DB CPU peak (%) | seconds with DB CPU >= 90% |")
print("|---|---|---|---|---|---|---|---|---|")
for c in adds:
    m = runs[c]
    pk = m["peak_db"]
    print("| %s | %s | %.0f | %d | %.0f | %.2f | %d | %.0f | %d |" % (
        job(c), m["cpu_model"], m["pgbench_tps"], m["add"], pk["db_per_s"], pk["ratio"], m["new_db_total"],
        m["pg_cpu_peak_pct"], len(m["pg_cpu_ge_90_s"])))
print()
if "restart-default" in runs:
    m = runs["restart-default"]
    print("`kubectl rollout restart` of the 4 Pods with the default strategy. Rollout time is from the command until `kubectl rollout status` returned.")
    print()
    print("| job | CPU model | pgbench tps | peak DB reads/s | rollout (s) |")
    print("|---|---|---|---|---|")
    print("| %s | %s | %.0f | %.0f | %.1f |" % (
        job("restart-default"), m["cpu_model"], m["pgbench_tps"], m["peak_db"]["db_per_s"], m["rollout_s"]))
    print()
if saturated("restart-default") and runs["restart-default"]["recovered_s"] is not None:
    per_second("restart-default")
elif saturated("add-4") and runs["add-4"]["recovered_s"] is not None:
    per_second("add-4")

print("## 4. Mitigations")
print()
rs = [c for c in RESTARTS if c in runs]
if rs:
    print("`kubectl rollout restart` of the 4 Pods. Overlapped reads started while another read of the same key was in flight in the same Pod. Peak pool wait is the highest per-second mean of the pgxpool acquires that found no idle connection, added when an acquire succeeds. Recovered is the time from the command until DB reads stayed below %s/s, no read was in flight and DB CPU was within %s points of the value before the command, for %s s." % (
        runs[rs[0]]["params"]["QUIET_READS"], runs[rs[0]]["params"]["QUIET_DB_CPU"], runs[rs[0]]["params"]["QUIET_SECONDS"]))
    print()
    print("| job | strategy | CPU model | pgbench tps | peak DB reads/s | overlapped reads | peak pool wait (ms) | rollout (s) | recovered (s) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for c in rs:
        m = runs[c]
        rec = "not within %s s" % m["params"]["MAX_OBS"] if m["recovered_s"] is None else "%.1f" % m["recovered_s"]
        print("| %s | %s | %s | %.0f | %.0f | %d | %.0f | %.1f | %s |" % (
            job(c), strategy(c), m["cpu_model"], m["pgbench_tps"], m["peak_db"]["db_per_s"], m["new_dup_total"],
            m["pool_wait_peak_ms"], m["rollout_s"], rec))
    print()
if "restart-default" in runs:
    r = runs["restart-default"]["resolved"]
    print("With 4 replicas the default strategy resolved to a `deployment.kubernetes.io/max-replicas` of %s. During the rollout at most %s Pods were not terminating and at least %s were available." % (
        r["max_replicas"], r["max_pods"], r["min_available"]))
