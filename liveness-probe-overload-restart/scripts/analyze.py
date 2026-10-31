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
PERIOD = 10
CLUSTER = os.environ.get("CLUSTER", "lpr")
PATHS = {"/livez": "Liveness", "/readyz": "Readiness"}


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


def kbin(t):
    return int((t // BIN) * BIN)


def median(v):
    v = sorted(v)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


timeline = {}
for l in open(P("timeline.txt")):
    p = l.split()
    timeline[" ".join(p[:-1])] = float(p[-1])
st = stages(cond)
c_spec = CONDITIONS[cond]
timeouts = {"Liveness": c_spec.get("liveness_timeout", 1), "Readiness": 1}

k6_t0 = None
reqs = []
for line in open(P("k6", "k6.log")) if os.path.exists(P("k6", "k6.log")) else []:
    p = line.rstrip("\n").split(" ", 5)
    if p[0] == "T0":
        k6_t0 = int(p[1]) / 1000
    elif p[0] == "r":
        err = p[5] if len(p) > 5 else ""
        cls = "ok" if p[3] == "200" else ("timeout" if "timeout" in err.lower() else "other")
        reqs.append((int(p[1]) / 1000, int(p[2]) / 1000, cls))

T0 = k6_t0 if cond == "capacity" else k6_t0 + st[0][1]


def rel(t):
    return t - T0


load_end = (sum(s for _, s, _ in st) - st[0][1]) if st and cond != "capacity" else None
end_rel = rel(timeline["measure_end"])
lo = -st[0][1] if st and cond != "capacity" else 0

pods_raw = jsonl("pods.jsonl")
pods_s = [x for x in pods_raw if "pods" in x]
names = sorted({p["pod"] for x in pods_s for p in x["pods"]})
short = {n: n.rsplit("-", 1)[-1] for n in names}

status_restarts = []
prev_rc = {}
for x in pods_s:
    for p in x["pods"]:
        n, rc = p["pod"], p["restarts"]
        if n in prev_rc and rc > prev_rc[n]:
            term = ((p.get("lastState") or {}).get("terminated")) or {}
            status_restarts.append({"name": n, "count": rc, "terminated": term})
        prev_rc[n] = rc

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

ev_occ = []
seen_cnt = {}
for e in evs:
    o = e["object"]
    uid = o["metadata"]["uid"]
    cnt = o.get("count") or 1
    if e["type"] == "DELETED" or seen_cnt.get(uid) == cnt:
        continue
    seen_cnt[uid] = cnt
    ev_occ.append({"recv": rel(e["recv"]), "kind": o["involvedObject"]["kind"], "name": o["involvedObject"]["name"],
                   "reason": o.get("reason"), "message": o.get("message", ""), "count": cnt, "raw": o})
ev_occ.sort(key=lambda e: e["recv"])

kills = [e for e in ev_occ if e["reason"] == "Killing" and "failed liveness probe" in e["message"] and e["name"] in short]
kills_by_pod = collections.defaultdict(list)
for e in kills:
    kills_by_pod[e["name"]].append(e["recv"])
restarts = [{"pod": short[e["name"]], "name": e["name"], "t": e["recv"]} for e in kills]

recvq = collections.defaultdict(list)
netstat = collections.defaultdict(list)
for line in open(P("recvq.txt")):
    p = line.split()
    if len(p) >= 6 and p[3] == "NETSTAT":
        netstat[p[1]].append((rel(float(p[0])), int(p[4]), int(p[5])))
    elif len(p) >= 6 and p[3].isdigit():
        recvq[(p[1], p[5].rsplit(":", 1)[-1])].append((rel(float(p[0])), int(p[3])))
overflows = {}
for n in names:
    s = sorted(netstat.get(n, []))
    overflows[short[n]] = {"ListenOverflows": s[-1][1] - s[0][1], "ListenDrops": s[-1][2] - s[0][2]} if s else None

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


results_at = collections.defaultdict(list)
prev = {}
for s in probe_scrapes:
    for k, v in s["m"].items():
        d = int(round(v - prev.get(k, 0.0)))
        if d > 0 and k[2] in ("successful", "failed"):
            results_at[(k[0], k[1])].append((s["t"], k[2], d))
    prev = s["m"]

logs = []
for fn in sorted(glob.glob(P("logs", "*.log"))):
    logs.append((os.path.basename(fn)[:-4], fn))
for fn in sorted(glob.glob(P("previous", "*.log"))):
    logs.append((re.sub(r"-\d+\.log$", "", os.path.basename(fn)), fn))
probe_lines = collections.defaultdict(list)
work_starts = []
work_by_pod = collections.defaultdict(list)
hz = []
for pod, fn in logs:
    for l in open(fn):
        p = l.split()
        if len(p) < 7 or p[0] != "req":
            continue
        if p[3] in PATHS:
            probe_lines[(pod, PATHS[p[3]])].append(rel(float(p[1])))
            if "/logs/" in fn:
                hz.append((pod, p[5]))
        elif p[3] == "/work":
            work_starts.append(rel(float(p[1])))
            work_by_pod[pod].append(rel(float(p[1])))
containers_expected = sum(1 + (prev_rc.get(n) or 0) for n in names)

ticks = []
phase_info = {}
TOL = 0.1


THRESHOLD = 3


def liveness_phase(lines):
    ref = [x for x in lines if lo <= x < 0] if cond != "capacity" else [x for x in lines if 0 <= x < 15]
    if not ref:
        return None
    r0 = ref[0]
    offs = [((x - r0 + PERIOD / 2) % PERIOD) - PERIOD / 2 for x in ref]
    return r0 + min(offs)


for n in names:
    for ptype in ("Liveness", "Readiness"):
        res_list = sorted(results_at.get((n, ptype), []))
        lines = sorted(probe_lines.get((n, ptype), []))
        if not res_list or not lines:
            continue
        to = timeouts[ptype]
        used = [False] * len(res_list)
        tk = []

        def take(T):
            for i, (u, result, d) in enumerate(res_list):
                if not used[i] and 0 < u - T <= to + 1.6:
                    used[i] = True
                    return result
            return None

        if ptype == "Liveness":
            phase = liveness_phase(lines)
            if phase is None:
                continue
            T = phase + int((min(lines) - phase + TOL) // PERIOD) * PERIOD
            while T < end_rel + PERIOD:
                tk.append({"pod": short[n], "name": n, "type": ptype, "T": T, "kubelet": take(T), "wait": None})
                T += PERIOD
        else:
            bounds = [-1e9] + kills_by_pod.get(n, []) + [end_rel + PERIOD]
            for a_, b_ in zip(bounds, bounds[1:]):
                seg = [x for x in lines if a_ <= x < b_]
                if not seg:
                    continue
                T = seg[0]
                consec = 0
                while T < b_:
                    r = take(T)
                    tk.append({"pod": short[n], "name": n, "type": ptype, "T": T, "kubelet": r, "wait": None})
                    if r == "failed":
                        consec += 1
                        if consec == THRESHOLD:
                            T += to
                            continue
                    elif r == "successful":
                        consec = 0
                    T += PERIOD
        phase_info[(short[n], ptype)] = [round(x["T"], 2) for x in tk[:1]]
        off_schedule = sum(d for (u, result, d), uu in zip(res_list, used) if not uu)
        unmatched = 0
        for x_s in lines:
            succ = [x for x in tk if x["kubelet"] == "successful" and x["wait"] is None and -TOL <= x_s - x["T"] < to + 0.3]
            if succ:
                succ[-1]["wait"] = x_s - succ[-1]["T"]
                continue
            kill_before = max([kt for kt in kills_by_pod.get(n, []) if kt < x_s], default=-1e9)
            cand = [x for x in tk if x["kubelet"] == "failed" and x["wait"] is None and kill_before < x["T"] <= x_s - to + 0.05]
            if cand:
                cand[0]["wait"] = x_s - cand[0]["T"]
            else:
                unmatched += 1
        phase_info[(short[n], ptype, "off_schedule_results")] = off_schedule
        phase_info[(short[n], ptype, "unmatched_log_lines")] = unmatched
        ticks.extend(x for x in tk if x["kubelet"] is not None)
ticks.sort(key=lambda x: x["T"])


def bucket(x):
    if x["wait"] is None:
        return "no record"
    if x["wait"] < 0.5:
        return "< 0.5 s"
    if x["wait"] < 1.0:
        return "0.5 to 1 s"
    return ">= 1 s"


wait_table = collections.Counter((bucket(x), x["type"], x["kubelet"]) for x in ticks if x["T"] >= lo)
live_fail = [x for x in ticks if x["type"] == "Liveness" and x["kubelet"] == "failed"]
live_fail_split = {"wait >= timeoutSeconds": sum(1 for x in live_fail if x["wait"] is not None and x["wait"] >= timeouts["Liveness"]),
                   "no record": sum(1 for x in live_fail if x["wait"] is None),
                   "wait < timeoutSeconds": sum(1 for x in live_fail if x["wait"] is not None and x["wait"] < timeouts["Liveness"])}
first_live_fail = live_fail[0] if live_fail else None


def waits_in(a, b):
    out = {}
    for ptype in ("Liveness", "Readiness"):
        w = [x["wait"] for x in ticks if x["type"] == ptype and a <= x["T"] < b and x["wait"] is not None]
        if w:
            out[ptype] = {"probes": len(w), "median_ms": round(median(w) * 1000, 1), "max_ms": round(max(w) * 1000, 1)}
    return out


schedule_check = {"before the 1.2C stage (t < 0)": waits_in(lo, 0)}

eps_watch = jsonl("eps-watch.jsonl")
eps_watch_log = json.load(open(P("eps-watch.json"))) if os.path.exists(P("eps-watch.json")) else {"errors": ["missing"]}
changes = [(rel(e["recv"]), e["ready"]) for e in eps_watch]
zero_seconds = 0.0
cur_ready = None
for i, (t, r) in enumerate(changes):
    nxt = changes[i + 1][0] if i + 1 < len(changes) else end_rel
    a, b = max(t, 0), min(nxt, end_rel)
    if r == 0 and b > a:
        zero_seconds += b - a
pod_unready = collections.defaultdict(list)
state = {}
for e in eps_watch:
    if "eps" not in e:
        continue
    t = rel(e["recv"])
    now = {x["pod"]: x["ready"] for x in e["eps"] if x["pod"]}
    for n in names:
        r = now.get(n) is True
        if n in state and state[n] and not r:
            pod_unready[n].append([t, None])
        if n in state and not state[n] and r and pod_unready[n] and pod_unready[n][-1][1] is None:
            pod_unready[n][-1][1] = t
        state[n] = r
readiness_outages = []
for n, ivs in pod_unready.items():
    for a_, b_ in ivs:
        if a_ < 0:
            continue
        if not any(a_ - 3 <= k <= (b_ if b_ is not None else end_rel) for k in kills_by_pod.get(n, [])):
            readiness_outages.append({"pod": short[n], "from": round(a_, 2), "to": round(b_, 2) if b_ is not None else None})
ready_changes = []
for t, r in changes:
    if not ready_changes or ready_changes[-1][1] != r:
        ready_changes.append((round(t, 2), r))

work_bins = collections.Counter(kbin(s) for s in work_starts)
rows = []
for b in range(kbin(lo), int(end_rel) + 1, BIN):
    row = {"t": b}
    for n in names:
        vals = [rq for t, rq in recvq.get((n, "8080"), []) if b <= t < b + BIN]
        row["rq_" + short[n]] = max(vals) if vals else None
    readys = [r for t, r in changes if b <= t < b + BIN]
    before = [r for t, r in changes if t < b]
    row["ready_min"] = min(readys + before[-1:]) if (readys or before) else None
    row["live_fail"] = cum_before(b + BIN, "Liveness", "failed") - cum_before(b, "Liveness", "failed")
    row["ready_fail"] = cum_before(b + BIN, "Readiness", "failed") - cum_before(b, "Readiness", "failed")
    row["live_fail_total"] = cum_before(b + BIN, "Liveness", "failed")
    row["restarts"] = ",".join(r["pod"] for r in restarts if b <= r["t"] < b + BIN)
    row["processed"] = work_bins.get(b, 0)
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
if watch["errors"]:
    invalid.append("event watch errors: %s" % watch["errors"][:3])
if gaps:
    invalid.append("event watch gaps: %s" % gaps[:5])
if eps_watch_log["errors"]:
    invalid.append("EndpointSlice watch errors: %s" % eps_watch_log["errors"][:3])

drained = None
if load_end:
    pods_rq = [n for n in names]
    lows = [all((r.get("rq_" + short[n]) or 0) <= 20 for n in pods_rq) for r in rows if 0 <= r["t"] and r["t"] + BIN <= load_end]
    lr = [r for r in rows if 0 <= r["t"] and r["t"] + BIN <= load_end]
    drained = next((r["t"] for i, r in enumerate(lr) if all(lows[i:])), None)
    if drained is not None:
        schedule_check["after the queue drained (t >= %d)" % drained] = waits_in(drained, load_end)

rate_per_pod = None
rate_by_pod = {}
if cond != "capacity" and len(st) == 3:
    done = busy = 0
    for n in names:
        secs = {int(t // 1) for t, rq in recvq.get((n, "8080"), []) if 0 <= t < OVER_SECONDS and rq > 0}
        cnt = sum(1 for x in work_by_pod.get(n, []) if 0 <= x < OVER_SECONDS and int(x // 1) in secs)
        rate_by_pod[short[n]] = {"busy_seconds": len(secs), "processed": cnt,
                                 "rps": round(cnt / len(secs), 1) if secs else None}
        done += cnt
        busy += len(secs)
    rate_per_pod = round(done / busy, 1) if busy else None
    if rate_per_pod is None or abs(rate_per_pod - C / 3) >= 0.05 * C / 3:
        invalid.append("processing rate %s rps per Pod in the seconds of the 1.2C stage with Recv-Q > 0 is 5%% or more "
                       "away from C/3 = %.1f" % (rate_per_pod, C / 3))

capacity = None
if cond == "capacity":
    ok = collections.Counter(int(t + d) for t, d, cls in reqs if cls == "ok")
    ks = sorted(ok)[10:-2]
    cap = round(sum(ok[k] for k in ks) / len(ks), 1) if ks else None
    capacity = {"vus": c_spec["vus"], "rps": cap, "vs_C_pct": round(100.0 * (cap - C) / C, 1) if cap else None}
    if cap is None or abs(cap - C) >= 0.05 * C:
        invalid.append("measured capacity %s rps is 5%% or more away from C = %d" % (cap, C))

kernel = None
for line in open(P("00-environment.txt")):
    if kernel == "":
        kernel = line.strip()
        break
    if line.startswith("$ uname -r"):
        kernel = ""

reset_events = []
for k in kills:
    for e in ev_occ:
        if (e["name"] == k["name"] and e["reason"] == "Unhealthy" and e["message"].startswith("Readiness")
                and "connection reset by peer" in e["message"] and k["recv"] - 1 <= e["recv"] <= k["recv"] + 5):
            reset_events.append({"pod": short[k["name"]], "kill": round(k["recv"], 2), "event": round(e["recv"], 2),
                                 "message": e["message"]})

running_at = {}
for l in open(P("timeline.txt")):
    p = l.split()
    if p[0] == "running":
        running_at[int(p[1])] = rel(float(p[2]))
first_live = {}
for s in probe_scrapes:
    for n in names:
        if n not in first_live and (total_at(s["m"], "Liveness", "successful", n) + total_at(s["m"], "Liveness", "failed", n)) > 0:
            first_live[n] = s["t"]

intervals = {}
for n, ts_ in kills_by_pod.items():
    intervals[short[n]] = [round(b - a, 2) for a, b in zip(ts_, ts_[1:])]

metrics = {
    "cond": cond, "C": C, "stages": st, "load_end": load_end, "t0": T0, "invalid": invalid, "dropped": dropped,
    "achieved": achieved, "event_watch": watch, "event_gaps": gaps, "eps_watch": eps_watch_log,
    "restarts": [{"pod": r["pod"], "t": round(r["t"], 2)} for r in restarts],
    "restart_intervals": intervals, "ready_zero_seconds": round(zero_seconds, 1), "ready_changes": ready_changes, "readiness_outages": readiness_outages,
    "overflows": overflows, "rows": rows, "pods": {short[n]: n for n in names},
    "wait_table": [[k[0], k[1], k[2], v] for k, v in sorted(wait_table.items())],
    "live_fail_split": live_fail_split,
    "first_live_fail": {k: first_live_fail[k] for k in ("pod", "T", "wait")} if first_live_fail else None,
    "ticks": [{k: x[k] for k in ("pod", "type", "T", "kubelet", "wait")} for x in ticks],
    "schedule_check": schedule_check,
    "phase_info": {"%s %s" % k[:2] + (" " + k[2] if len(k) > 2 else ""): v for k, v in phase_info.items()},
    "reset_events": reset_events, "rate_per_pod": rate_per_pod, "rate_by_pod": rate_by_pod, "capacity": capacity, "kernel": kernel,
    "claim1": {"probe_requests": len(hz), "distinct_pod_source_pairs": len(set(hz)),
               "source_addrs": sorted({b.rsplit(":", 1)[0] for _, b in hz})},
    "stagger_running": [round(running_at[k], 2) for k in sorted(running_at)],
    "stagger_first_liveness": sorted(round(v, 2) for v in first_live.values()),
    "log_coverage": {"containers_expected": containers_expected, "log_files": len(logs)},
}
if status_restarts and kills:
    k0 = kills[0]
    sr = next((r for r in status_restarts if r["name"] == k0["name"] and r["count"] == 1), None)
    first = {"pod": short[k0["name"]], "name": k0["name"], "t": round(k0["recv"], 2),
             "lastState_terminated": sr["terminated"] if sr else None}
    pf = P("previous", "%s-1.log" % k0["name"])
    if os.path.exists(pf):
        first["previous_nonreq_last"] = [l for l in open(pf).read().splitlines()[1:] if not l.startswith("req ")][-8:]
    live = [e for e in ev_occ if e["name"] == k0["name"] and e["reason"] == "Unhealthy" and e["message"].startswith("Liveness")]
    first["event_liveness_failed"] = live[0]["raw"] if live else None
    first["event_killing"] = k0["raw"]
    metrics["first"] = first
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
if capacity:
    print("- closed loop with %d VUs: %s responses per second, %s%% from C" % (capacity["vus"], capacity["rps"], capacity["vs_C_pct"]))
print("- restarts (Killing events, t): %s" % (", ".join("%s %d" % (r["pod"], round(r["t"])) for r in restarts) or "none"))
print("- seconds with 0 Ready endpoints from t=0 (EndpointSlice watch): %.1f" % zero_seconds)
print("- ListenOverflows / ListenDrops per Pod: %s" % overflows)
print("- /work processed per Pod-second with Recv-Q > 0 in the 1.2C stage: %s rps (%s)" % (rate_per_pod, rate_by_pod))
print("- failed liveness probes: %s" % live_fail_split)
print("- probe waits, schedule check: %s" % schedule_check)
print()
print("| wait | Liveness ok | Liveness failed | Readiness ok | Readiness failed |")
print("|---|---|---|---|---|")
for bk in ("< 0.5 s", "0.5 to 1 s", ">= 1 s", "no record"):
    print("| %s | %d | %d | %d | %d |" % (bk, wait_table[(bk, "Liveness", "successful")], wait_table[(bk, "Liveness", "failed")],
                                        wait_table[(bk, "Readiness", "successful")], wait_table[(bk, "Readiness", "failed")]))
print()
print("| t (s) | " + " | ".join("Recv-Q " + short[n] for n in names) +
      " | Ready endpoints (min) | liveness failed | readiness failed | restarted | /work processed |")
print("|" + "---|" * (len(names) + 6))
for r in rows:
    print("| %d | " % r["t"] + " | ".join(f(r["rq_" + short[n]]) for n in names) +
          " | %s | %d | %d | %s | %d |" % (f(r["ready_min"]), r["live_fail"], r["ready_fail"], r["restarts"], r["processed"]))
