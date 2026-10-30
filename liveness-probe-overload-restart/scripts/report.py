import json
import math
import os
import re
import statistics
import sys

runs = {}
for d in sys.argv[1:]:
    p = os.path.join(d, "metrics.json")
    if not os.path.exists(p):
        continue
    m = json.load(open(p))
    tail = re.search(r"-(\d+)$", os.path.basename(os.path.normpath(d)))
    m["run"] = int(tail.group(1)) if tail else 0
    runs.setdefault(m["cond"], []).append(m)
for v in runs.values():
    v.sort(key=lambda m: m["run"])
NAME = {"P1": "baseline (no change)", "P2": "no 1.2C stage", "P3": "probes on a second port (8081)",
        "P4": "liveness timeoutSeconds 5", "P5": "no liveness probe", "capacity": "capacity measurement"}
NEVER = math.inf


def f(v, nd=1):
    if v is None:
        return "-"
    if v == NEVER:
        return "not by the end"
    return ("%." + str(nd) + "f") % v


def g(v):
    if v is None:
        return "-"
    if v == NEVER:
        return "not by the end"
    return ("%d" % v) if float(v).is_integer() else ("%.1f" % v)


def of(cond):
    return runs.get(cond, [])


def block(lines):
    print("```")
    for l in lines:
        print(l)
    print("```")


def measured(m):
    return [r for r in m["rows"] if r["t"] >= 0]


def med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def spread(table):
    counts = {len(next(iter(cols.values()))) for cols in table.values()}
    if counts == {1}:
        print("One run per condition.")
        print()
        return
    diff = []
    for cond, cols in table.items():
        for col, vals in cols.items():
            if len(set(vals)) > 1:
                diff.append("%s, %s: %s" % (NAME[cond], col, ", ".join(g(v) for v in vals)))
    print("Medians of %s runs per condition. %s" % (
        "/".join(str(c) for c in sorted(counts)),
        ("Values that differed between runs, in run order: " + "; ".join(diff) + ".") if diff
        else "Every run of a condition gave the same values."))
    print()


def stage_at(m, t):
    start = -m["stages"][0][1]
    for r, sec, fr in m["stages"]:
        if start <= t < start + sec:
            return "%d (%.2fC)" % (r, fr)
        start += sec
    return "-"


def num(s):
    return None if s in (None, "-") else float(s)


def gaps(values):
    v = sorted(x for x in values if x is not None)
    return [round(b - a, 1) for a, b in zip(v, v[1:])]


def recovery(m):
    rows = measured(m)
    pods = sorted(m["pods"])
    low = [all((r.get("rq_" + p) or 0) <= 20 for p in pods) for r in rows]
    return next((r["t"] for i, r in enumerate(rows) if all(low[i:])), NEVER)


def peak(m):
    return max((r.get("rq_" + p) or 0) for r in m["rows"] for p in sorted(m["pods"]))


def run_label(m):
    return "run %d" % m["run"] if m["run"] else "the run"


print("# liveness-probe-overload-restart")
print()
print("t is seconds from the start of the 1.2C stage; in \"%s\", which has none, from the switch to 0.9C. C = %s rps." % (
    NAME["P2"], ", ".join(sorted({str(m["C"]) for v in runs.values() for m in v}))))
print()

base = next((m for m in of("P1") if m.get("first")), of("P1")[0] if of("P1") else None)

print("## 3. Reproduce")
print()
if base:
    m = base
    pods = sorted(m["pods"])
    print("### %s timeline (5 s bins, %s)" % (NAME["P1"], run_label(m)))
    print()
    print("Recv-Q is the maximum in the bin.")
    print()
    print("| t | load (rps) | " + " | ".join("Recv-Q " + p for p in pods) + " | restarted |")
    print("|" + "---|" * (len(pods) + 3))
    for r in m["rows"]:
        if -5 <= r["t"] <= 75:
            print("| %d | %s | " % (r["t"], stage_at(m, r["t"])) + " | ".join(f(r.get("rq_" + p), 0) for p in pods) +
                  " | %s |" % r["restarts"])
    print()
    rest = [r for r in m["rows"] if r["t"] >= 80]
    if rest:
        rq = max((r.get("rq_" + p) or 0) for r in rest for p in pods)
        loads = sorted({stage_at(m, r["t"]) for r in rest} - {"-"})
        print("From t=%d to t=%d (load %s): Recv-Q at most %d on any Pod, %d restarts." % (
            rest[0]["t"], rest[-1]["t"], "; ".join(x.replace(" (", " rps, ").rstrip(")") for x in loads), rq,
            sum(len([x for x in r["restarts"].split(",") if x]) for r in rest)))
        print()
    fr = m.get("first")
    if fr:
        rq = dict((t, q) for t, q in fr["recvq"])
        before = max((t for t in rq if t < fr["first_liveness_failure_seen"]), default=None)
        near = min(rq, key=lambda t: abs(t - 2.3)) if rq else None
        print("Values for estimating the wait of a probe (%s, %s, pod %s):" % (NAME["P1"], run_label(m), fr["pod"]))
        print()
        print("- Recv-Q on 8080 at t=%s: %d" % (f(near), rq[near]))
        print("- Recv-Q on 8080 at t=%s, the last sample before the first failed liveness probe was seen: %d" % (
            f(before), rq[before]))
        print("- first failed liveness probe of %s seen in /metrics/probes (scraped every second) at t=%s" % (
            fr["pod"], f(fr["first_liveness_failure_seen"])))
for m in of("capacity"):
    print("- %s: closed loop with %d VUs, %s responses per second for the 3 Pods, %s per Pod" % (
        NAME["capacity"], m["capacity_vus"], m["capacity_rps"], f(m["capacity_rps"] / 3)))
print()
if of("P2"):
    v = of("P2")
    cols = {"restarts": [len(m["restarts"]) for m in v], "max Recv-Q": [peak(m) for m in v],
            "failed liveness probes": [sum(r["live_fail"] for r in m["rows"]) for m in v]}
    print("%s (0.75C → 0.90C): %s restarts, Recv-Q at most %s, %s failed liveness probes." % (
        NAME["P2"], g(med(cols["restarts"])), g(med(cols["max Recv-Q"])), g(med(cols["failed liveness probes"]))))
    print()
    spread({"P2": cols})

print("## 4. Check in your own cluster (%s: the first Pod to restart)" % NAME["P1"])
print()
if base and base.get("first"):
    m = base
    fr = m["first"]
    print("%s, %s, pod %s, restart %d at t=%s" % (NAME["P1"], run_label(m), fr["name"], fr["count"], f(fr["finished"])))
    print()
    print("`.status.containerStatuses[0].lastState.terminated` (containerID left out):")
    print()
    term = fr["lastState_terminated"]
    block(json.dumps({k: term.get(k) for k in ("exitCode", "reason", "startedAt", "finishedAt")}, indent=2).splitlines())
    print()
    print("Events (lastTimestamp, type, reason, involvedObject, message):")
    print()
    for key in ("event_liveness_failed", "event_killing"):
        e = fr.get(key)
        if e:
            block(["%s  %s  %s  %s/%s  %s" % (e["lastTimestamp"], e["type"], e["reason"], e["involvedObject"]["kind"].lower(),
                                              e["involvedObject"]["name"], e["message"])])
            print()
    lines = fr.get("previous_nonreq_last", [])
    a = next((i for i, l in enumerate(lines) if "Handling signal: term" in l), None)
    b = next((i for i, l in enumerate(lines) if "Shutting down: Master" in l), None)
    print("`kubectl logs %s --previous`, from \"Handling signal: term\" to \"Shutting down: Master\", per-request lines left out:" % fr["name"])
    print()
    block(lines[a:b + 1] if a is not None and b is not None else lines)
    print()
    rows = measured(m)
    inc = [r for r in rows if r["live_fail"]]
    if inc:
        s, e = inc[0]["t"], inc[-1]["t"]
        inside = sum(r["live_fail"] for r in rows if s <= r["t"] <= e)
        outside = sum(r["live_fail"] for r in m["rows"] if not s <= r["t"] <= e)
        print("`prober_probe_total{probe_type=\"Liveness\",result=\"failed\"}` summed over the Pods: it first increased in the "
              "5 s bin starting at t=%d and last increased in the bin starting at t=%d, by %d in total between them, and by %d "
              "in all other bins of the run." % (s, e, inside, outside))
        print()
else:
    print("No restart in %s." % NAME["P1"])
    print()

print("## 5. What changing the probe settings does")
print()
print("Load in every condition in the table: 0.75C for 15 s, then 1.2C for 60 s, then 0.9C for 105 s, back to 0.9C at t=60 "
      "(C = %s rps)." % ", ".join(sorted({str(m["C"]) for v in runs.values() for m in v})))
print()
print("The last column is the first 5 s bin from which the maximum Recv-Q of every Pod stays at 20 or less in every "
      "bin until the end of the measurement. A first restart is counted only in the runs that had one.")
print()
print("| condition | restarts | first restart (t) | max Recv-Q (any Pod) | seconds with 0 Ready endpoints "
      "| Recv-Q of every Pod at most 20 from the bin at t |")
print("|---|---|---|---|---|---|")
table = {}
for c in ("P1", "P3", "P4", "P5"):
    v = of(c)
    if not v:
        continue
    cols = {"restarts": [len(m["restarts"]) for m in v],
            "first restart": [round(m["first_restart"], 1) if m["first_restart"] is not None else None for m in v],
            "max Recv-Q": [peak(m) for m in v], "seconds with 0 Ready endpoints": [m["ready_zero_seconds"] for m in v],
            "Recv-Q at most 20 from": [recovery(m) for m in v]}
    table[c] = cols
    print("| %s | %s | %s | %s | %s | %s |" % (
        NAME[c], g(med(cols["restarts"])), f(med(cols["first restart"])), g(med(cols["max Recv-Q"])),
        g(med(cols["seconds with 0 Ready endpoints"])), g(med(cols["Recv-Q at most 20 from"]))))
print()
spread(table)

print("## Numbers not used")
print()
for c in ("P1", "P2"):
    v = of(c)
    if v:
        print("- %s, new connection per probe: %s /healthz requests in the logs of the current containers, %s distinct "
              "(pod, source address:port) pairs, source addresses %s" % (
                  NAME[c], ", ".join(str(m["claim1"]["healthz_requests"]) for m in v),
                  ", ".join(str(m["claim1"]["distinct_pod_source_pairs"]) for m in v),
                  ", ".join(sorted({a for m in v for a in m["claim1"]["source_addrs"]}))))
for c in sorted(runs):
    v = runs[c]
    run_gaps = "; ".join(", ".join(g(x) for x in gaps(m["stagger_running"].values())) for m in v)
    live = [[num(s["first_liveness_seen"]) for s in m["stagger"]] for m in v]
    live_gaps = "; ".join(", ".join(g(x) for x in gaps(l)) for l in live) if any(
        x is not None for l in live for x in l) else "no liveness probe"
    print("- %s, Pod start (per run): seconds between the containers becoming running %s; between their first liveness "
          "probes %s" % (NAME[c], run_gaps, live_gaps))
print()
print("| condition | valid runs | dropped_iterations | achieved rps per stage (target), lowest and highest run | event watch errors | event count gaps | app logs (files / containers) |")
print("|---|---|---|---|---|---|---|")
for c in sorted(runs, key=lambda c: list(NAME).index(c)):
    v = runs[c]
    stages = []
    for i, a in enumerate(v[0]["achieved"]):
        vals = [m["achieved"][i]["achieved_rps"] for m in v]
        stages.append("%s–%s (%d)" % (min(vals), max(vals), a["target_rps"]) if min(vals) != max(vals)
                      else "%s (%d)" % (vals[0], a["target_rps"]))
    print("| %s | %d of %d | %d | %s | %d | %d | %d / %d |" % (
        NAME[c], sum(1 for m in v if not m["invalid"]), len(v), sum(m["dropped"] for m in v), ", ".join(stages) or "-",
        sum(len(m["event_watch"]["errors"]) for m in v), sum(len(m["event_gaps"]) for m in v),
        sum(m["log_coverage"]["log_files"] for m in v), sum(m["log_coverage"]["containers_expected"] for m in v)))
for c in sorted(runs):
    for m in runs[c]:
        for i in m["invalid"]:
            print()
            print("RUN INVALID (%s, %s): %s" % (NAME[c], run_label(m), i))
