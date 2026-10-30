import collections
import glob
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conditions import C, CONDITIONS, OVER_SECONDS, stages

res, cond = sys.argv[1], sys.argv[2]
BIN = 5
CLUSTER = os.environ.get("CLUSTER", "lpr")


def P(*a):
    return os.path.join(res, *a)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def jsonl(name):
    if not os.path.exists(P(name)):
        return []
    return [json.loads(line) for line in open(P(name))]


def f(v, nd=0):
    return "-" if v is None else ("%." + str(nd) + "f") % v


def pct(v, q):
    if not v:
        return None
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))]


def kbin(t):
    return int((t // BIN) * BIN)


timeline = {}
for l in open(P("timeline.txt")):
    p = l.split()
    timeline[" ".join(p[:-1])] = float(p[-1])
st = stages(cond)

k6_t0 = None
reqs = []
for line in open(P("k6", "k6.log")) if os.path.exists(P("k6", "k6.log")) else []:
    p = line.rstrip("\n").split(" ", 5)
    if p[0] == "T0":
        k6_t0 = int(p[1]) / 1000
    elif p[0] == "r":
        err = p[5] if len(p) > 5 else ""
        if p[3] == "200":
            cls = "ok"
        elif "reset by peer" in err:
            cls = "reset"
        elif "timeout" in err.lower():
            cls = "timeout"
        else:
            cls = "other"
        reqs.append((int(p[1]) / 1000, int(p[2]) / 1000, cls, p[3], p[4], err))

T0 = k6_t0 if cond == "capacity" else k6_t0 + st[0][1]


def rel(t):
    return t - T0


ret_at = OVER_SECONDS if len(st) == 3 else None
end_rel = rel(timeline["measure_end"])

pods_raw = jsonl("pods.jsonl")
pods_s = [x for x in pods_raw if "pods" in x]
eps_s = jsonl("eps.jsonl")
names = sorted({p["pod"] for x in pods_s for p in x["pods"]})
short = {n: n.rsplit("-", 1)[-1] for n in names}

restarts = []
prev_rc = {}
for x in pods_s:
    for p in x["pods"]:
        n, rc = p["pod"], p["restarts"]
        if n in prev_rc and rc > prev_rc[n]:
            term = ((p.get("lastState") or {}).get("terminated")) or {}
            restarts.append({"pod": short[n], "name": n, "count": rc, "detected": rel(x["t"]),
                             "finished": rel(ts(term["finishedAt"])) if term.get("finishedAt") else None,
                             "terminated": term})
        prev_rc[n] = rc
restarts.sort(key=lambda r: (r["finished"] if r["finished"] is not None else r["detected"], r["pod"]))

ready_series = [(rel(x["t"]), sum(1 for e in x["eps"] if e["ready"]), {e["pod"]: e["ready"] for e in x["eps"]})
                for x in eps_s]
measured = [x for x in ready_series if x[0] >= (-st[0][1] if st else 0)]

outages = []
for n in names:
    cur = None
    for t, _, m in measured:
        if m.get(n) is not True:
            if cur is None:
                cur = {"pod": short[n], "start": t, "end": None}
        elif cur is not None:
            cur["end"] = t
            outages.append(cur)
            cur = None
    if cur is not None:
        outages.append(cur)
for o in outages:
    hit = any(r["pod"] == o["pod"] and r["finished"] is not None and o["start"] - 3 <= r["finished"] <= (o["end"] or 1e9)
              for r in restarts)
    o["reason"] = "restart" if hit else "readiness"
outages.sort(key=lambda o: (o["start"], o["pod"]))

recvq = collections.defaultdict(list)
for line in open(P("recvq.txt")):
    p = line.split()
    if len(p) < 6 or not p[3].isdigit():
        continue
    recvq[(p[1], p[5].rsplit(":", 1)[-1])].append((rel(float(p[0])), int(p[3])))

probe_scrapes = []
cur = None
for line in open(P("probes.txt")):
    if line.startswith("# t="):
        if cur:
            probe_scrapes.append(cur)
        parts = line.split()
        cur = {"t": rel(float(parts[1][2:])), "m": {}} if len(parts) == 2 else None
        continue
    if cur is None:
        continue
    m = re.match(r'(\w+)\{(.*)\} (\S+)', line)
    if m and m.group(1) == "prober_probe_total":
        lb = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
        cur["m"][(lb.get("pod"), lb.get("probe_type"), lb.get("result"))] = float(m.group(3))
if cur:
    probe_scrapes.append(cur)


def total_at(m, ptype, result, pod=None):
    return int(round(sum(v for k, v in m.items() if k[1] == ptype and k[2] == result and (pod is None or k[0] == pod))))


def cum_before(t, ptype, result, pod=None):
    best = 0
    for s in probe_scrapes:
        if s["t"] >= t:
            break
        best = total_at(s["m"], ptype, result, pod)
    return best


log_files = sorted(glob.glob(P("logs", "*.log"))) + sorted(glob.glob(P("previous", "*.log")))
containers_expected = sum(1 + (prev_rc.get(n) or 0) for n in names)
hz = []
for fn in log_files:
    pod = os.path.basename(fn)
    for l in open(fn):
        p = l.split()
        if len(p) < 7 or p[0] != "req":
            continue
        if p[3] == "/healthz" and fn.startswith(P("logs")):
            hz.append((pod, p[5], float(p[1])))

k6bins = collections.defaultdict(collections.Counter)
lat = collections.defaultdict(list)
for t, d, cls, *_ in reqs:
    b = kbin(rel(t + d))
    k6bins[b][cls] += 1
    if cls == "ok":
        lat[b].append(d)

lo = -st[0][1] if st else 0
rows = []
for b in range(kbin(lo), int(end_rel) + 1, BIN):
    row = {"t": b}
    for n in names:
        vals = [rq for t, rq in recvq.get((n, "8080"), []) if b <= t < b + BIN]
        row["rq_" + short[n]] = max(vals) if vals else None
    rs = [c for t, c, _ in ready_series if b <= t < b + BIN]
    row["ready_min"] = min(rs) if rs else None
    for ptype, key in (("Liveness", "live_fail"), ("Readiness", "ready_fail")):
        row[key] = cum_before(b + BIN, ptype, "failed") - cum_before(b, ptype, "failed")
    row["live_fail_total"] = cum_before(b + BIN, "Liveness", "failed")
    row["restarts"] = ",".join(r["pod"] for r in restarts if r["finished"] is not None and b <= r["finished"] < b + BIN)
    c = k6bins.get(b, collections.Counter())
    row.update({k: c[k] for k in ("ok", "timeout", "reset", "other")})
    row["p50"] = pct(lat.get(b), 0.5)
    rows.append(row)

invalid = []
sm = json.load(open(P("k6", "summary.json"))) if os.path.exists(P("k6", "summary.json")) else {}
dropped = int(((sm.get("metrics") or {}).get("dropped_iterations") or {}).get("values", {}).get("count", 0))
achieved = []
begin = 0
for rate, dur, frac in st:
    a, b_ = k6_t0 + begin, k6_t0 + begin + dur
    n = sum(1 for t, *_ in reqs if a <= t < b_)
    achieved.append({"target_rps": rate, "seconds": dur, "iterations": n, "achieved_rps": round(n / dur, 1)})
    if abs(n / dur - rate) > 0.02 * rate:
        invalid.append("achieved rate %.1f rps vs target %d rps in the %d s stage" % (n / dur, rate, dur))
    begin += dur
if dropped:
    invalid.append("dropped_iterations = %d" % dropped)
if not reqs:
    invalid.append("no k6 requests recorded")
bad_nodes = sorted({p["node"] for x in pods_s for p in x["pods"] if p["node"] and p["node"] != CLUSTER + "-worker"})
if bad_nodes:
    invalid.append("app Pods on %s" % bad_nodes)
final_pods = json.load(open(P("pods-final.json")))["items"]
k6pods = [p for p in final_pods if p["metadata"].get("labels", {}).get("job-name") == "k6"]
if not k6pods or any(p["spec"].get("nodeName") != CLUSTER + "-control-plane" for p in k6pods):
    invalid.append("k6 Pod not on %s-control-plane" % CLUSTER)
if any("error" in x for x in pods_raw):
    invalid.append("status poll errors: %d" % sum(1 for x in pods_raw if "error" in x))
if not probe_scrapes or any(len(s["m"]) == 0 for s in probe_scrapes if s["t"] >= lo):
    invalid.append("kubelet probe metrics missing")

watch = json.load(open(P("events-watch.json")))
evs = jsonl("events.jsonl")
last = {}
gaps = []
for e in evs:
    o = e["object"]
    uid = o["metadata"]["uid"]
    cnt = o.get("count") or 1
    if e["type"] != "DELETED":
        if uid in last and cnt not in (last[uid], last[uid] + 1):
            gaps.append((o.get("reason"), o["involvedObject"]["name"], last[uid], cnt))
        if uid not in last and e["type"] == "MODIFIED" and cnt != 1:
            gaps.append((o.get("reason"), o["involvedObject"]["name"], None, cnt))
    last[uid] = cnt
for o in json.load(open(P("events-final.json")))["items"]:
    if last.get(o["metadata"]["uid"]) != (o.get("count") or 1):
        gaps.append((o.get("reason"), o["involvedObject"]["name"], last.get(o["metadata"]["uid"]), o.get("count")))
if watch["errors"]:
    invalid.append("event watch errors: %s" % watch["errors"][:3])
if gaps:
    invalid.append("event watch gaps: %s" % gaps[:5])

ev_occ = []
seen_cnt = {}
for e in evs:
    o = e["object"]
    uid = o["metadata"]["uid"]
    cnt = o.get("count") or 1
    if e["type"] == "DELETED" or seen_cnt.get(uid) == cnt:
        continue
    seen_cnt[uid] = cnt
    when = ts(o.get("lastTimestamp") or o.get("eventTime") or o["metadata"]["creationTimestamp"])
    ev_occ.append({"t": round(rel(when), 1), "recv": round(rel(e["recv"]), 2), "kind": o["involvedObject"]["kind"],
                   "pod": short.get(o["involvedObject"]["name"], o["involvedObject"]["name"]), "type": o.get("type"),
                   "reason": o.get("reason"), "count": cnt, "message": o.get("message", ""), "raw": o})
ev_occ.sort(key=lambda e: e["recv"])

seen_at = collections.defaultdict(list)
for a, b, t in hz:
    seen_at[(a, b)].append(t)
claim1 = {"healthz_requests": len(hz), "distinct_pod_source_pairs": len(seen_at),
          "source_addrs": dict(collections.Counter(b.rsplit(":", 1)[0] for _, b, _ in hz)),
          "repeated_pairs_seconds_apart": sorted(round(max(v) - min(v), 1) for v in seen_at.values() if len(v) > 1)}

first_live = {}
first_ready = {}
for s in probe_scrapes:
    for n in names:
        if n not in first_live and (total_at(s["m"], "Liveness", "successful", n) + total_at(s["m"], "Liveness", "failed", n)) > 0:
            first_live[n] = s["t"]
        if n not in first_ready and (total_at(s["m"], "Readiness", "successful", n) + total_at(s["m"], "Readiness", "failed", n)) > 0:
            first_ready[n] = s["t"]
running_at = {}
for l in open(P("timeline.txt")):
    p = l.split()
    if p[0] == "running":
        running_at[int(p[1])] = rel(float(p[2]))
first_started = {}
for x in pods_s:
    for p in x["pods"]:
        run = ((p.get("state") or {}).get("running")) or {}
        if p["pod"] not in first_started and run.get("startedAt") and p["restarts"] == 0:
            first_started[p["pod"]] = rel(ts(run["startedAt"]))
stagger = [{"pod": short[n], "first_readiness_seen": f(first_ready.get(n), 0),
            "first_liveness_seen": f(first_live.get(n), 0)} for n in sorted(names, key=lambda n: first_ready.get(n, 1e9))]
stagger_running = {k: round(v, 1) for k, v in sorted(running_at.items())}

first_restart = restarts[0]["finished"] if restarts else None
last_restart = restarts[-1]["finished"] if restarts else None
ready_back = None
if ret_at is not None:
    tail = [(t, c) for t, c, _ in ready_series if t >= ret_at]
    for i, (t, c) in enumerate(tail):
        if all(cc == 3 for _, cc in tail[i:]):
            ready_back = t
            break
ready_min = min((c for t, c, _ in ready_series if t >= 0), default=None)
zero_secs = sum(1 for t, c, _ in ready_series if t >= 0 and c == 0)
totals = collections.Counter(r[2] for r in reqs)
max_rq = {short[n]: max([rq for t, rq in recvq.get((n, "8080"), []) if t >= 0], default=None) for n in names}
max_rq_probe = {short[n]: max([rq for t, rq in recvq.get((n, "8081"), []) if t >= 0], default=None) for n in names}

metrics = {
    "cond": cond, "C": C, "stages": st, "return_at": ret_at, "t0": T0, "invalid": invalid, "dropped": dropped,
    "achieved": achieved, "event_watch": watch, "event_gaps": gaps,
    "restarts": [{k: v for k, v in r.items() if k != "terminated"} |
                 {"exitCode": r["terminated"].get("exitCode"), "reason": r["terminated"].get("reason")} for r in restarts],
    "first_restart": first_restart, "last_restart": last_restart, "ready_back_to_3": ready_back,
    "ready_min": ready_min, "ready_zero_seconds": zero_secs, "k6_totals": dict(totals),
    "max_recvq": max_rq, "max_recvq_probe_port": max_rq_probe, "outages": outages, "claim1": claim1, "rows": rows,
    "stagger": stagger, "stagger_running": stagger_running, "pods": {short[n]: n for n in names},
    "log_coverage": {"containers_expected": containers_expected, "log_files": len(log_files)},
}
if cond == "capacity":
    ok = collections.Counter(int(t + d) for t, d, cls, *_ in reqs if cls == "ok")
    ks = sorted(ok)[10:-2]
    cap = round(sum(ok[k] for k in ks) / len(ks), 1) if ks else None
    metrics["capacity_vus"] = CONDITIONS["capacity"]["vus"]
    metrics["capacity_rps"] = cap
    metrics["capacity_latency_p50_ms"] = round(pct([d for t, d, cls, *_ in reqs if cls == "ok"], 0.5) * 1000, 1)
    metrics["capacity_vs_C_pct"] = round(100.0 * (cap - C) / C, 1) if cap else None
    if cap is None or abs(cap - C) >= 0.05 * C:
        invalid.append("measured capacity %s rps is 5%% or more away from C = %d" % (cap, C))

if restarts:
    r0 = restarts[0]
    first = {"pod": r0["pod"], "name": r0["name"], "count": r0["count"], "finished": r0["finished"],
             "lastState_terminated": r0["terminated"]}
    pf = P("previous", "%s-%d.log" % (r0["name"], r0["count"]))
    if os.path.exists(pf):
        lines = open(pf).read().splitlines()[1:]
        first["previous_nonreq_last"] = [l for l in lines if not l.startswith("req ")][-8:]
    live = [e for e in ev_occ if e["pod"] == r0["pod"] and e["reason"] == "Unhealthy" and e["message"].startswith("Liveness")]
    kill = [e for e in ev_occ if e["pod"] == r0["pod"] and "failed liveness probe" in e["message"]]
    first["event_liveness_failed"] = live[0]["raw"] if live else None
    first["event_killing"] = kill[0]["raw"] if kill else None
    ff = next((s["t"] for s in probe_scrapes if total_at(s["m"], "Liveness", "failed", r0["name"]) > 0), None)
    first["first_liveness_failure_seen"] = ff
    lo_t = (ff if ff is not None else r0["finished"]) - 15
    first["recvq"] = [(round(t, 1), rq) for t, rq in recvq.get((r0["name"], "8080"), []) if lo_t <= t <= r0["finished"] + 3]
    metrics["first"] = first
metrics["events"] = [{k: v for k, v in e.items() if k != "raw"} for e in ev_occ if e["kind"] == "Pod"]
json.dump(metrics, open(P("metrics.json"), "w"), indent=1, default=list)

print("# %s (%s)" % (cond, "INVALID" if invalid else "valid"))
print()
for i in invalid:
    print("RUN INVALID: " + i)
if invalid:
    print()
print("C = %d rps. Stages: %s." % (C, ", ".join("%d rps for %d s" % (r, s) for r, s, _ in st) or "closed loop"))
print("t = 0 is %s." % ("when k6 started" if cond == "capacity" else "the end of the first stage"))
print()
if cond == "capacity":
    print("- closed loop with %d VUs: %s responses per second (200), %s%% from C, p50 %s ms" % (
        metrics["capacity_vus"], metrics["capacity_rps"], metrics["capacity_vs_C_pct"], metrics["capacity_latency_p50_ms"]))
print("- restarts: %d, first at t=%s s, last at t=%s s" % (len(restarts), f(first_restart, 1), f(last_restart, 1)))
print("- Ready endpoints from t=0: min %s, %d s at 0, back to 3 for good at t=%s s" % (ready_min, zero_secs, f(ready_back, 1)))
print("- k6: %s, dropped_iterations %d" % (dict(totals), dropped))
print("- max Recv-Q on 8080 from t=0: %s" % max_rq)
print("- first liveness probe seen (t): %s" % ", ".join("%s %s" % (s["pod"], s["first_liveness_seen"]) for s in stagger))
print()
print("| t (s) | " + " | ".join("Recv-Q " + short[n] for n in names) +
      " | Ready endpoints | liveness failed | readiness failed | restarted | ok | timeout | reset | p50 ms |")
print("|" + "---|" * (len(names) + 9))
for r in rows:
    print("| %d | " % r["t"] + " | ".join(f(r["rq_" + short[n]]) for n in names) +
          " | %s | %d | %d | %s | %d | %d | %d | %s |" % (
              f(r["ready_min"]), r["live_fail"], r["ready_fail"], r["restarts"], r["ok"], r["timeout"], r["reset"],
              f(r["p50"] and r["p50"] * 1000)))
print()
if restarts:
    print("| pod | restartCount | terminated at (t) | exitCode | reason |")
    print("|---|---|---|---|---|")
    for r in restarts:
        print("| %s | %d | %s | %s | %s |" % (r["pod"], r["count"], f(r["finished"], 1), r["terminated"].get("exitCode"),
                                           r["terminated"].get("reason")))
    print()
if outages:
    print("| pod | out of the endpoints from (t) | to (t) | reason |")
    print("|---|---|---|---|")
    for o in outages:
        print("| %s | %s | %s | %s |" % (o["pod"], f(o["start"], 1), f(o["end"], 1), o["reason"]))
