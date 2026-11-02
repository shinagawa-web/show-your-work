import json
import os
import sys

runs = {}
elapsed = {}
for d in sys.argv[1:]:
    p = os.path.join(d, "metrics.json")
    if not os.path.exists(p):
        continue
    m = json.load(open(p))
    runs[m["cond"]] = m
    el = {}
    for line in open(os.path.join(d, "elapsed.txt")):
        k, v = line.split()
        el[k] = v
    elapsed[m["cond"]] = el

if not runs:
    print("RUN INVALID: no metrics.json in %s" % " ".join(sys.argv[1:]))
    sys.exit(0)

mit = runs["redis"]["add"] if "redis" in runs else int(next(iter(runs.values()))["params"]["MITIGATION_N"])

SECTIONS = {"restart-default": "3, 4", "restart-surge1": "4", "redis": "4", "add-%d" % mit: "3, 4"}


def job(cond):
    return "%s (%s)" % (SECTIONS.get(cond, "3"), cond)


adds = sorted((c for c in runs if c.startswith("add-")), key=lambda c: int(c[4:]))
order = adds + [c for c in ("restart-default", "restart-surge1", "redis") if c in runs]


def restart_table(conds):
    print("| job | strategy | peak DB reads/s | p99 peak (ms) | rollout (s) |")
    print("|---|---|---|---|---|")
    for c in conds:
        m = runs[c]
        st = "default (maxSurge 25%, maxUnavailable 25%, minReadySeconds 0)" if c == "restart-default" else \
            "maxSurge 1, maxUnavailable 0, minReadySeconds %s" % m["params"]["MIN_READY"]
        print("| %s | %s | %.0f | %.0f | %.1f |" % (
            job(c), st, m["peak_db"]["db_per_s"], m["p99_peak"]["p99_ms"], m["rollout_s"]))
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
print("Pods added to 4 Pods with filled caches. DB reads are counted by the app when a request misses the local cache, summed over all Pods per second. p99 is per second from k6, the highest second from the operation until 3 s after DB reads fell below 10/s.")
print()
print("| job | Pods added | peak DB reads/s | peak / all requests | DB reads by the added Pods | p99 peak (ms) |")
print("|---|---|---|---|---|---|")
for c in adds:
    m = runs[c]
    pk = m["peak_db"]
    print("| %s | %d | %.0f | %.2f | %d | %.0f |" % (
        job(c), m["add"], pk["db_per_s"], pk["ratio"], m["new_db_total"], m["p99_peak"]["p99_ms"]))
print()
if "restart-default" in runs:
    print("`kubectl rollout restart` of the 4 Pods. Rollout time is from the command until `kubectl rollout status` returned.")
    print()
    restart_table(["restart-default"])

print("## 4. Mitigations")
print()
rs = [c for c in ("restart-default", "restart-surge1") if c in runs]
if rs:
    print("`kubectl rollout restart` of the 4 Pods with each rolling update strategy.")
    print()
    restart_table(rs)
ms = [c for c in ("add-%d" % mit, "redis") if c in runs]
if ms:
    print("%d Pods added to 4 Pods with filled caches in each job." % mit)
    print()
    print("| job | read path on a local cache miss | peak DB reads/s | peak / all requests | DB reads by the added Pods | p99 peak (ms) |")
    print("|---|---|---|---|---|---|")
    path = {"add-%d" % mit: "PostgreSQL", "redis": "Redis, then PostgreSQL"}
    for c in ms:
        m = runs[c]
        print("| %s | %s | %.0f | %.2f | %d | %.0f |" % (
            job(c), path[c], m["peak_db"]["db_per_s"], m["peak_db"]["ratio"], m["new_db_total"],
            m["p99_peak"]["p99_ms"]))
