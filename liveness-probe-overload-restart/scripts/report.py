import collections
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
BUCKETS = ("< 0.5 s", "0.5 to 1 s", ">= 1 s", "no record")


def f(v, nd=1):
    return "-" if v is None else ("%." + str(nd) + "f") % v


def g(v):
    if v is None:
        return "-"
    return ("%d" % v) if float(v).is_integer() else ("%.1f" % v)


def of(cond):
    return runs.get(cond, [])


def block(lines):
    print("```")
    for l in lines:
        print(l)
    print("```")


def med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def cell(vals, fmt=g):
    vals = [v for v in vals if v is not None]
    if not vals:
        return "-"
    lo, hi = min(vals), max(vals)
    return fmt(med(vals)) if lo == hi else "%s (%s–%s)" % (fmt(med(vals)), fmt(lo), fmt(hi))


def split_cell(vals, never_text):
    n = len(vals)
    nev = sum(1 for v in vals if v == NEVER)
    rest = [v for v in vals if v != NEVER]
    if nev == 0:
        return cell(rest)
    if nev == n:
        return never_text
    counts = collections.Counter(rest)
    return "%s in %d of %d runs, %s" % (never_text, nev, n, ", ".join(
        "%s in %d" % (g(v), c) for v, c in sorted(counts.items())))


def note(nruns):
    if nruns == 1:
        print("One run per condition.")
    else:
        print("Medians of %d runs per condition; ranges across runs in parentheses." % nruns)
    print()


def measured(m):
    return [r for r in m["rows"] if r["t"] >= 0]


def stage_at(m, t):
    start = -m["stages"][0][1]
    for r, sec, fr in m["stages"]:
        if start <= t < start + sec:
            return "%d (%.2fC)" % (r, fr)
        start += sec
    return "-"


def recovery(m):
    rows = [r for r in measured(m) if r["t"] + 5 <= m["load_end"]]
    pods = sorted(m["pods"])
    low = [all((r.get("rq_" + p) or 0) <= 20 for p in pods) for r in rows]
    return next((r["t"] for i, r in enumerate(rows) if all(low[i:])), NEVER)


def peak(m):
    return max((r.get("rq_" + p) or 0) for r in m["rows"] for p in sorted(m["pods"]))


def overflows(m):
    return sum((v or {}).get("ListenOverflows", 0) for v in m["overflows"].values())


def wait_counts(m):
    return {(b, t, k): c for b, t, k, c in m["wait_table"]}


def run_label(m):
    return "run %d" % m["run"] if m["run"] else "the run"


def maxrun(conds):
    return max([len(of(c)) for c in conds] or [1])


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
    print("Recv-Q is the maximum in the bin. A restart is placed at the time the Killing Event was received.")
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

if of("P1"):
    v = of("P1")
    print("### %s: wait of each probe and its result" % NAME["P1"])
    print()
    print("The wait is from the probe's scheduled time to the time the app started processing it. Liveness probes are "
          "scheduled every periodSeconds; readiness probes every periodSeconds from the first one in each container and "
          "from the probe the kubelet runs when the Pod becomes unready. \"No record\" is a probe that the kubelet sent and whose processing is not in the app "
          "logs. All Pods from t=-15, counted by the kubelet's result (prober_probe_total).")
    print()
    print("| wait | liveness successful | liveness failed | readiness successful | readiness failed |")
    print("|---|---|---|---|---|")
    for b in BUCKETS:
        print("| %s | %s |" % (b, " | ".join(str(sum(wait_counts(m).get((b, t, k), 0) for m in v))
                                            for t in ("Liveness", "Readiness") for k in ("successful", "failed"))))
    print()
    print("Summed over %s." % ("the %d runs" % len(v) if len(v) > 1 else "the run"))
    print()
    if base and base.get("first_live_fail"):
        x = base["first_live_fail"]
        print("First failed liveness probe in the timeline (%s): pod %s, scheduled at t=%s, %s." % (
            run_label(base), x["pod"], f(x["T"], 2),
            "processing started %s s later" % f(x["wait"], 2) if x["wait"] is not None else "no record in the app logs"))
        print()
    iv = [x for m in v for xs in m["restart_intervals"].values() for x in xs]
    if iv:
        print("Seconds between two restarts of the same Pod (Killing Events): median %s, from %s to %s (%d intervals)." % (
            f(med(iv)), f(min(iv)), f(max(iv)), len(iv)))
        print()
    ret = v[0]["stages"][1][1]
    after = sum(1 for m in v if any(r["t"] >= ret for r in m["restarts"]))
    unready = sum(1 for m in v if m.get("readiness_outages"))
    print("After the return to 0.9C at t=%d, a restart in %d of %d runs; a Pod out of the endpoints by readiness, without "
          "a restart, in %d of %d runs." % (ret, after, len(v), unready, len(v)))
    print()
    resets = [len(m["reset_events"]) for m in v]
    ex = next((e for m in v for e in m["reset_events"]), None)
    print("Readiness probe failures with \"connection reset by peer\" within 5 s after a Killing Event of the same Pod: %s." % cell(resets))
    if ex:
        print()
        block([ex["message"]])
    print()
    print("ListenOverflows summed over the Pods: %s." % cell([overflows(m) for m in v]))
    print()
if of("P2"):
    v = of("P2")
    fails = [sum(r["live_fail"] + r["ready_fail"] for r in m["rows"]) for m in v]
    print("%s (0.75C → 0.90C): %s restarts, Recv-Q at most %s, %s failed probes." % (
        NAME["P2"], cell([len(m["restarts"]) for m in v]), cell([peak(m) for m in v]), cell(fails)))
    print()
    note(len(v))

print("## 4. Check in your own cluster (%s: the first Pod to restart)" % NAME["P1"])
print()
if base and base.get("first"):
    m = base
    fr = m["first"]
    print("%s, %s, pod %s, Killing Event at t=%d" % (NAME["P1"], run_label(m), fr["name"], round(fr["t"])))
    print()
    print("`.status.containerStatuses[0].lastState.terminated` (containerID left out):")
    print()
    term = fr["lastState_terminated"] or {}
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
if of("P1"):
    m0 = of("P1")[0]
    print("Load in every condition in the table: 0.75C for 15 s, then 1.2C for 60 s, then 0.9C for 105 s, back to 0.9C at "
          "t=60 (C = %s rps)." % m0["C"])
    print()
    print("Restarts are counted from the Killing Events. Seconds with 0 Ready endpoints come from a watch on the "
          "EndpointSlice. The queue column is the first 5 s bin from which the maximum Recv-Q of every Pod stays at 20 or "
          "less in every bin while the load runs, up to the bin starting at t=%d; the load ends at t=%d. Failed liveness "
          "probes are split into those the app started processing after timeoutSeconds and those with no record in the app "
          "logs." % (m0["load_end"] - 5, m0["load_end"]))
    print()
print("| condition | restarts | first restart (t) | max Recv-Q (any Pod) | ListenOverflows | seconds with 0 Ready endpoints "
      "| Recv-Q of every Pod at most 20 from the bin at t | failed liveness probes: wait >= timeoutSeconds / no record |")
print("|---|---|---|---|---|---|---|---|")
for c in ("P1", "P3", "P4", "P5"):
    v = of(c)
    if not v:
        continue
    firsts = [round(m["restarts"][0]["t"]) if m["restarts"] else NEVER for m in v]
    lf = "%s / %s" % (cell([m["live_fail_split"]["wait >= timeoutSeconds"] for m in v]),
                      cell([m["live_fail_split"]["no record"] for m in v])) if c != "P5" else "no liveness probe"
    print("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
        NAME[c], cell([len(m["restarts"]) for m in v]), split_cell(firsts, "no restart"), cell([peak(m) for m in v]),
        cell([overflows(m) for m in v]), cell([m["ready_zero_seconds"] for m in v], lambda x: f(x)),
        split_cell([recovery(m) for m in v], "not while the load ran"), lf))
print()
note(maxrun(("P1", "P3", "P4", "P5")))

print("## Numbers not used")
print()
for c in ("P1", "P2"):
    v = of(c)
    if v:
        print("- %s, new connection per probe: %s /livez and /readyz requests in the logs of the current containers, %s "
              "distinct (pod, source address:port) pairs, source addresses %s" % (
                  NAME[c], ", ".join(str(m["claim1"]["probe_requests"]) for m in v),
                  ", ".join(str(m["claim1"]["distinct_pod_source_pairs"]) for m in v),
                  ", ".join(sorted({a for m in v for a in m["claim1"]["source_addrs"]}))))
for c in sorted(runs, key=lambda c: list(NAME).index(c)):
    v = runs[c]

    def gaps(xs):
        xs = sorted(xs)
        return ", ".join(f(b - a) for a, b in zip(xs, xs[1:]))
    print("- %s, Pod start (per run): seconds between the containers becoming running %s; between their first liveness "
          "probes %s" % (NAME[c], "; ".join(gaps(m["stagger_running"]) for m in v),
                         "; ".join(gaps(m["stagger_first_liveness"]) for m in v) if c != "P5" else "no liveness probe"))
for c in sorted(runs, key=lambda c: list(NAME).index(c)):
    if c == "capacity":
        continue
    per_run = []
    for m in runs[c]:
        parts = []
        for win, d in m["schedule_check"].items():
            parts.append("%s: %s" % (win, ", ".join("%s %d probes, median %s ms, max %s ms" % (
                t, x["probes"], x["median_ms"], x["max_ms"]) for t, x in d.items()) or "no probes"))
        per_run.append("%s: %s" % (run_label(m), "; ".join(parts)))
    print("- %s, wait of successful probes (schedule check; the liveness phase is taken from t=-15 to 0): %s" % (
        NAME[c], " / ".join(per_run)))
print("- kernel: %s" % ", ".join(sorted({str(m["kernel"]) for v in runs.values() for m in v})))
print()
print("| condition | valid runs | dropped_iterations | achieved rps per stage (target) | /work processed per Pod-second with Recv-Q > 0 in the 1.2C stage (rps; C/3 = %s) | event watch errors | event count gaps | EndpointSlice watch errors | app logs (files / containers) |" % (
    f(int(next(iter(runs.values()))[0]["C"]) / 3) if runs else "-"))
print("|---|---|---|---|---|---|---|---|---|")
for c in sorted(runs, key=lambda c: list(NAME).index(c)):
    v = runs[c]
    stg = []
    for i, a in enumerate(v[0]["achieved"]):
        stg.append("%s (%d)" % (cell([m["achieved"][i]["achieved_rps"] for m in v], lambda x: f(x)), a["target_rps"]))
    if c == "capacity":
        rate = "capacity %s rps for 3 Pods" % cell([m["capacity"]["rps"] for m in v], lambda x: f(x))
    else:
        rate = cell([m["rate_per_pod"] for m in v], lambda x: f(x))
    print("| %s | %d of %d | %d | %s | %s | %d | %d | %d | %d / %d |" % (
        NAME[c], sum(1 for m in v if not m["invalid"]), len(v), sum(m["dropped"] for m in v), ", ".join(stg) or "-", rate,
        sum(len(m["event_watch"]["errors"]) for m in v), sum(len(m["event_gaps"]) for m in v),
        sum(len(m["eps_watch"]["errors"]) for m in v),
        sum(m["log_coverage"]["log_files"] for m in v), sum(m["log_coverage"]["containers_expected"] for m in v)))
for c in sorted(runs, key=lambda c: list(NAME).index(c)):
    for m in runs[c]:
        for i in m["invalid"]:
            print()
            print("RUN INVALID (%s, %s): %s" % (NAME[c], run_label(m), i))
