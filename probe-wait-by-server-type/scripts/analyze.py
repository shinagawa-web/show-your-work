import collections
import glob
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conditions import C, OVER_SECONDS, PODS, WARM, capacity, stages, work_ms
from servers import SERVERS

res, server, mode = sys.argv[1], sys.argv[2], sys.argv[3]
CLUSTER = os.environ.get("CLUSTER", "pws")
GRACE = 30


def P(*a):
    return os.path.join(res, *a)


def jsonl(name):
    if not os.path.exists(P(name)):
        return []
    return [json.loads(line) for line in open(P(name))]


def med(v):
    v = [x for x in v if x is not None]
    return statistics.median(v) if v else None


def mx(v):
    v = [x for x in v if x is not None]
    return max(v) if v else None


def rnd(v, nd=3):
    return None if v is None else round(v, nd)


timeline = {}
for l in open(P("timeline.txt")):
    p = l.split()
    timeline[" ".join(p[:-1])] = float(p[-1])
st = stages(mode)

k6_t0 = None
reqs = []
for line in open(P("k6", "k6.log")) if os.path.exists(P("k6", "k6.log")) else []:
    p = line.rstrip("\n").split(" ", 5)
    if p[0] == "T0":
        k6_t0 = int(p[1]) / 1000
    elif p[0] == "r" and len(p) >= 5:
        err = p[5] if len(p) > 5 else ""
        status = p[3]
        if status == "200":
            cls = "ok"
        elif "timeout" in err.lower():
            cls = "timeout"
        elif "connection reset by peer" in err:
            cls = "reset"
        elif "connection refused" in err:
            cls = "refused"
        elif err.endswith("EOF") or "EOF" in err:
            cls = "eof"
        elif status == "0":
            cls = "other connection error"
        else:
            cls = "status " + status
        reqs.append((int(p[1]) / 1000, int(p[2]) / 1000, cls, err))
if k6_t0 is None:
    k6_t0 = timeline["measure_start"]

T0 = k6_t0 if mode == "capacity" else k6_t0 + WARM


def rel(t):
    return t - T0


load_end = OVER_SECONDS + st[2][1] if st else 1e9
end_rel = rel(timeline["measure_end"])

pods_raw = jsonl("pods.jsonl")
pods_s = [x for x in pods_raw if "pods" in x]
names = sorted({p["pod"] for x in pods_s for p in x["pods"]})
short = {n: n.rsplit("-", 1)[-1] for n in names}
final_rc = {}
for x in pods_s:
    for p in x["pods"]:
        final_rc[p["pod"]] = p["restarts"]

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
    ev_occ.append({"t": round(rel(e["recv"]), 2), "name": o["involvedObject"]["name"], "reason": o.get("reason"),
                   "message": o.get("message", ""), "count": cnt})
ev_occ.sort(key=lambda e: e["t"])
kills = [e for e in ev_occ if e["reason"] == "Killing" and "liveness" in e["message"] and e["name"] in short]
event_counts = collections.Counter("%s %s" % (e["reason"], e["message"].split(":")[0][:60]) for e in ev_occ
                                   if e["name"] in short and e["t"] >= -WARM)

probes = collections.defaultdict(list)
for line in open(P("prober.log")) if os.path.exists(P("prober.log")) else []:
    p = line.split()
    if len(p) == 5 and p[0] == "p":
        probes[p[1]].append((rel(float(p[2])), float(p[3]), p[4]))
probe_first_over = {}
probe_max = {}
probe_max_before = {}
probe_max_over = {}
probe_max_t = {}
probe_results = collections.Counter()
for n in names:
    rows = sorted(probes.get(n, []))
    over = [t for t, w, r in rows if t >= 0 and w > 1.0]
    probe_first_over[short[n]] = rnd(over[0], 2) if over else None
    probe_max[short[n]] = rnd(mx([w for t, w, r in rows if 0 <= t < load_end]))
    probe_max_before[short[n]] = rnd(mx([w for t, w, r in rows if -WARM <= t < 0]))
    probe_max_over[short[n]] = rnd(mx([w for t, w, r in rows if 0 <= t < OVER_SECONDS]))
    top = max(((w, t) for t, w, r in rows if 0 <= t < load_end), default=None)
    probe_max_t[short[n]] = rnd(top[1], 2) if top else None
    for t, w, r in rows:
        if t >= -WARM:
            probe_results["200" if r == "200" else r.split(":")[0]] += 1
firsts = [v for v in probe_first_over.values() if v is not None]

listen = collections.defaultdict(list)
netstat = collections.defaultdict(list)
fds = collections.defaultdict(list)
cpu = collections.defaultdict(list)
hostcpu = []
for line in open(P("node.txt")):
    p = line.split()
    if len(p) >= 6 and p[3] == "NETSTAT":
        netstat[p[1]].append((rel(float(p[0])), int(p[4]), int(p[5])))
    elif len(p) >= 5 and p[3] == "CPU":
        cpu[p[1]].append((rel(float(p[0])), int(p[4])))
    elif len(p) >= 8 and p[3] == "HOSTCPU":
        v = [int(x) for x in p[4:]]
        hostcpu.append((rel(float(p[0])), sum(v), v[3] + v[4]))
    elif len(p) >= 6 and p[3] == "FD":
        fds[p[1]].append((rel(float(p[0])), int(p[4]), int(p[5])))
    elif len(p) >= 6 and p[3].isdigit() and p[5].endswith(":8080"):
        listen[p[1]].append((rel(float(p[0])), int(p[3])))
recvq_max = {short[n]: mx([q for t, q in listen.get(n, []) if 0 <= t < load_end]) for n in names}
first_kill = {}
for k in kills:
    first_kill.setdefault(short[k["name"]], k["t"])
recvq_max_before_kill = {short[n]: mx([q for t, q in listen.get(n, []) if 0 <= t < first_kill.get(short[n], load_end)])
                         for n in names}
overflows = {}
for n in names:
    s = sorted(x for x in netstat.get(n, []) if x[0] >= -WARM)
    overflows[short[n]] = (s[-1][1] - s[0][1]) if s else None
fd_base = {short[n]: med([c for t, c, k in fds.get(n, []) if -WARM <= t < 0]) for n in names}
fd_max = {short[n]: mx([c for t, c, k in fds.get(n, []) if 0 <= t < load_end]) for n in names}
fd_max_t = {}
for n in names:
    rows = [(c, t) for t, c, k in fds.get(n, []) if 0 <= t < load_end]
    fd_max_t[short[n]] = rnd(max(rows)[1], 1) if rows else None



def rates(samples):
    out = []
    for (t0, u0), (t1, u1) in zip(samples, samples[1:]):
        if t1 > t0 and u1 >= u0:
            out.append((t1, (u1 - u0) / 1e6 / (t1 - t0)))
    return out


cpu_cores = {short[n]: rates(sorted(cpu.get(n, []))) for n in names}
host_busy = []
for (t0, a0, i0), (t1, a1, i1) in zip(hostcpu, hostcpu[1:]):
    if a1 > a0:
        host_busy.append((t1, 1 - (i1 - i0) / (a1 - a0)))
cpu_max_over = {k: rnd(mx([c for t, c in v if 0 <= t < OVER_SECONDS]), 2) for k, v in cpu_cores.items()}
cpu_med_over = {k: rnd(med([c for t, c in v if 0 <= t < OVER_SECONDS]), 2) for k, v in cpu_cores.items()}
host_busy_over = {"median": rnd(med([b for t, b in host_busy if 0 <= t < OVER_SECONDS]), 2),
                  "max": rnd(mx([b for t, b in host_busy if 0 <= t < OVER_SECONDS]), 2),
                  "before": rnd(med([b for t, b in host_busy if -WARM <= t < 0]), 2)}


def host_at(t):
    near = [b for tt, b in host_busy if t - 0.5 <= tt <= t + 1.5]
    return rnd(mx(near), 2)


stats = collections.defaultdict(list)
logs = [(os.path.basename(fn)[:-4], fn) for fn in sorted(glob.glob(P("logs", "*.log")))]
logs += [(re.sub(r"-\d+\.log$", "", os.path.basename(fn)), fn) for fn in sorted(glob.glob(P("previous", "*.log")))]
for pod, fn in logs:
    for l in open(fn):
        p = l.split()
        if len(p) < 8 or p[0] != "stat":
            continue
        row = {"from": rel(float(p[1])), "to": rel(float(p[2])), "pid": p[3], "arrived": int(p[4]), "done": int(p[5]),
               "waiting": int(p[6]), "inflight": int(p[7])}
        for kv in p[8:]:
            k, v = kv.split("=", 1)
            try:
                row[k] = float(v)
            except ValueError:
                row[k] = v
        stats[pod].append(row)
for v in stats.values():
    v.sort(key=lambda r: r["to"])


def stat_max(key, a=0, b=None):
    b = load_end if b is None else b
    out = {}
    for n in names:
        vals = [r[key] for r in stats.get(n, []) if key in r and isinstance(r[key], float) and a <= r["to"] < b]
        if key in ("waiting", "inflight"):
            vals = [r[key] for r in stats.get(n, []) if a <= r["to"] < b]
        out[short[n]] = mx(vals)
    return out


def stat_first(key, threshold):
    out = {}
    for n in names:
        hit = [r["to"] for r in stats.get(n, []) if isinstance(r.get(key), float) and r[key] >= threshold and r["to"] >= 0]
        out[short[n]] = rnd(hit[0], 1) if hit else None
    return out


def stat_at(key, t):
    out = {}
    for n in names:
        rows = [r for r in stats.get(n, []) if isinstance(r.get(key), float) and r["to"] <= t]
        out[short[n]] = rows[-1][key] if rows else None
    return out


def stat_base(key):
    return {short[n]: med([r[key] for r in stats.get(n, []) if isinstance(r.get(key), float) and -WARM <= r["to"] < 0])
            for n in names}


server_metrics = {
    "waiting_max": stat_max("waiting"), "inflight_max": stat_max("inflight"),
    "waiting_at_overload_end": {short[n]: ([r["waiting"] for r in stats.get(n, []) if r["to"] <= OVER_SECONDS] or [None])[-1]
                                for n in names},
}
if server.startswith("tomcat"):
    server_metrics.update({
        "busy_max": stat_max("busy"), "busy_values": {short[n]: sorted({r["busy"] for r in stats.get(n, []) if isinstance(r.get("busy"), float)})
                                                       for n in names},
        "busy_reached_200_t": stat_first("busy", 200), "conns_max": stat_max("conns"), "queue_max": stat_max("queue"),
        "executor": sorted({r.get("executor") for n in names for r in stats.get(n, [])} - {None}),
    })
if server == "node":
    server_metrics.update({
        "lag_p99_ms_max": stat_max("lag_p99_ms"), "lag_p99_ms_max_in_1.2C": stat_max("lag_p99_ms", 0, OVER_SECONDS), "lag_p99_ms_before": stat_base("lag_p99_ms"),
        "lag_max_ms_max": stat_max("lag_max_ms"), "lag_min_ms_before": stat_base("lag_min_ms"),
        "lag_mean_ms_before": stat_base("lag_mean_ms"), "lag_mean_ms_max": stat_max("lag_mean_ms"),
        "conns_max": stat_max("conns"),
    })
if server == "go":
    server_metrics.update({
        "goroutines_max": stat_max("goroutines"), "goroutines_before": stat_base("goroutines"),
        "goroutines_at_overload_end": stat_at("goroutines", OVER_SECONDS), "conns_max": stat_max("conns"),
        "conns_at_overload_end": stat_at("conns", OVER_SECONDS),
    })

probe_spikes = []
for n in names:
    for t, w, r in sorted(probes.get(n, [])):
        if 0 <= t < load_end and 0.05 < w:
            ivals = [r["to"] - r["from"] for r in stats.get(n, []) if r["to"] >= t and r["from"] <= t + w]
            probe_spikes.append({"pod": short[n], "t": rnd(t, 2), "wait": rnd(w), "host_cpu_busy": host_at(t),
                                 "stat_interval_max": rnd(mx(ivals), 2)})
probe_spikes.sort(key=lambda x: x["t"])
win_end = {short[n]: min(OVER_SECONDS, first_kill.get(short[n], OVER_SECONDS)) for n in names}


def in_win(n, t):
    return 0 <= t < win_end[short[n]]


win = {"end": win_end, "probe_first_over_1s": {}, "probe_max": {}, "recvq_max": {}, "queue_max": {}, "conns_max": {},
       "inflight_max": {}, "goroutines_max": {}, "busy_max": {}, "busy_first_200": {}, "fd_max": {}}
for n in names:
    k = short[n]
    rows = sorted((t, w) for t, w, r in probes.get(n, []) if in_win(n, t))
    over = [t for t, w in rows if w > 1.0]
    win["probe_first_over_1s"][k] = rnd(over[0], 2) if over else None
    win["probe_max"][k] = rnd(mx([w for t, w in rows]))
    win["recvq_max"][k] = mx([q for t, q in listen.get(n, []) if in_win(n, t)])
    win["fd_max"][k] = mx([c for t, c, x in fds.get(n, []) if in_win(n, t)])
    st_rows = [r for r in stats.get(n, []) if in_win(n, r["to"])]
    for key in ("queue", "conns", "inflight", "goroutines", "busy"):
        vals = [r[key] for r in st_rows if isinstance(r.get(key), (int, float)) and not isinstance(r.get(key), bool)]
        win[key + "_max"][k] = mx(vals)
    hit = [r["to"] for r in st_rows if isinstance(r.get("busy"), float) and r["busy"] >= 200]
    win["busy_first_200"][k] = rnd(hit[0], 1) if hit else None
probe_windows = {}
for n in names:
    out = {}
    for label, a, b in (("1.2C", 0, OVER_SECONDS), ("after", OVER_SECONDS, load_end)):
        rows = [(w, t) for t, w, r in probes.get(n, []) if a <= t < b]
        if not rows:
            out[label] = None
            continue
        w, t = max(rows)
        ivals = [r["to"] - r["from"] for r in stats.get(n, []) if r["to"] >= t and r["from"] <= t + w]
        out[label] = {"wait": rnd(w), "t": rnd(t, 2), "stat_interval_max": rnd(mx(ivals), 2)}
    probe_windows[short[n]] = out
counts = collections.Counter(cls for t, d, cls, e in reqs)
by_sec = collections.defaultdict(collections.Counter)
for t, d, cls, e in reqs:
    by_sec[int((rel(t + d)) // 1)][cls] += 1
restart_windows = []
kt = sorted(k["t"] for k in kills)
for i, k in enumerate(sorted(kills, key=lambda k: k["t"])):
    a = k["t"] - 1
    b = kt[i + 1] - 1 if i + 1 < len(kt) else end_rel
    c = collections.Counter(cls for t, d, cls, e in reqs if a <= rel(t + d) < b and cls in ("reset", "eof", "refused"))
    near = collections.Counter(cls for t, d, cls, e in reqs if a <= rel(t + d) < k["t"] + 2 and cls in ("reset", "eof"))
    restart_windows.append({"pod": short[k["name"]], "kill_t": k["t"], "until_t": round(b, 2), "reset": c["reset"],
                            "eof": c["eof"], "refused": c["refused"], "reset_within_2s": near["reset"], "eof_within_2s": near["eof"]})
for w in restart_windows:
    n = next(x for x in names if short[x] == w["pod"])
    rows = [r for r in stats.get(n, []) if r["to"] <= w["kill_t"]]
    last_row = rows[-1] if rows else {}
    q = [q for t, q in listen.get(n, []) if t <= w["kill_t"]]
    w["at_kill"] = {k: last_row.get(k) for k in ("to", "waiting", "inflight", "queue", "conns") if k in last_row}
    w["at_kill"]["recvq"] = q[-1] if q else None
err_samples = {}
for t, d, cls, e in reqs:
    if cls != "ok" and cls not in err_samples:
        err_samples[cls] = e

eps_watch = jsonl("eps-watch.jsonl")
eps_watch_log = json.load(open(P("eps-watch.json"))) if os.path.exists(P("eps-watch.json")) else {"errors": ["missing"]}
changes = [(rel(e["recv"]), e["ready"]) for e in eps_watch]
first_unready = {}
for e in eps_watch:
    if "eps" not in e or rel(e["recv"]) < 0:
        continue
    for x in e["eps"]:
        if x["pod"] in short and x["ready"] is not True and short[x["pod"]] not in first_unready:
            first_unready[short[x["pod"]]] = round(rel(e["recv"]), 2)
zero_seconds = 0.0
min_ready = None
for i, (t, r) in enumerate(changes):
    nxt = changes[i + 1][0] if i + 1 < len(changes) else end_rel
    a, b = max(t, 0), min(nxt, end_rel)
    if b > a:
        min_ready = r if min_ready is None else min(min_ready, r)
        if r == 0:
            zero_seconds += b - a

pod_ready = collections.defaultdict(list)
for e in eps_watch:
    if "eps" not in e:
        continue
    now = {x["pod"]: x["ready"] is True for x in e["eps"] if x["pod"]}
    for n in names:
        pod_ready[n].append((rel(e["recv"]), now.get(n, False)))


def ready_at(n, t):
    st_ = None
    for tt, r in pod_ready.get(n, []):
        if tt > t:
            break
        st_ = r
    return st_


refuse_ivals = {"kill_to_unready": [], "zero_ready": []}
for k in kills:
    n = k["name"]
    if not ready_at(n, k["t"]):
        continue
    end = next((tt for tt, r in pod_ready.get(n, []) if tt >= k["t"] and not r), end_rel)
    refuse_ivals["kill_to_unready"].append((k["t"], end))
for i, (t, r) in enumerate(changes):
    nxt = changes[i + 1][0] if i + 1 < len(changes) else end_rel
    if r == 0:
        refuse_ivals["zero_ready"].append((t, nxt))
refused_split = collections.Counter()
for t, d, cls, e in reqs:
    if cls != "refused":
        continue
    te = rel(t + d)
    if any(a <= te < b for a, b in refuse_ivals["kill_to_unready"]):
        refused_split["from a liveness Killing to the Pod not Ready"] += 1
    elif any(a <= te < b for a, b in refuse_ivals["zero_ready"]):
        refused_split["while 0 endpoints were Ready"] += 1
    else:
        refused_split["other"] += 1

probe_scrapes = 0
probe_scrape_empty = 0
last_scrape = {}
scrape_series = []
cur = None
seen = False


def close_scrape():
    global probe_scrapes, probe_scrape_empty
    if cur is not None and cur >= timeline["measure_start"]:
        probe_scrapes += 1
        if not seen:
            probe_scrape_empty += 1


for line in open(P("probes.txt")):
    if line.startswith("# t="):
        close_scrape()
        parts = line.split()
        cur = float(parts[1][2:]) if len(parts) == 2 else None
        seen = False
        continue
    m = re.match(r'(\w+)\{(.*)\} (\S+)', line)
    if m and m.group(1) == "prober_probe_total" and cur is not None:
        lb = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
        if lb.get("pod", "").startswith("app-"):
            seen = True
            last_scrape[(lb.get("probe_type"), lb.get("result"), lb.get("pod"))] = float(m.group(3))
            scrape_series.append((rel(cur), lb.get("pod"), lb.get("probe_type"), lb.get("result"), float(m.group(3))))
close_scrape()
kubelet_probes = collections.Counter()
for k, v in last_scrape.items():
    kubelet_probes["%s %s" % (k[0], k[1])] += int(v)
readiness = {}
for n in names:
    rows = collections.defaultdict(dict)
    for t, pod, ptype, result, v in scrape_series:
        if pod == n and ptype == "Readiness":
            rows[t][result] = v
    ts_ = sorted(rows)
    at_start = next((rows[t] for t in ts_ if t >= -WARM), {})
    last_v = rows[ts_[-1]] if ts_ else {}
    run = best = 0
    prev = at_start
    for t in [t for t in ts_ if t >= -WARM]:
        cur_v = rows[t]
        df = cur_v.get("failed", 0) - prev.get("failed", 0)
        ds = cur_v.get("successful", 0) - prev.get("successful", 0)
        if ds > 0:
            run = 0
        if df > 0:
            run += int(df)
            best = max(best, run)
        prev = cur_v
    readiness[short[n]] = {"failed_total": int(last_v.get("failed", 0)),
                           "failed_from_t-15": int(last_v.get("failed", 0) - at_start.get("failed", 0)),
                           "max_consecutive_failed_from_t-15": best}

invalid = []
sm = json.load(open(P("k6", "summary.json"))) if os.path.exists(P("k6", "summary.json")) else {}
dropped = int(((sm.get("metrics") or {}).get("dropped_iterations") or {}).get("values", {}).get("count", 0))
achieved = []
begin = 0
for rate, dur, frac in st:
    a, b_ = k6_t0 + begin, k6_t0 + begin + dur
    n = sum(1 for t, *_ in reqs if a <= t < b_)
    achieved.append({"target_rps": rate, "seconds": dur, "achieved_rps": round(n / dur, 1)})
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
for label, want in (("k6", CLUSTER + "-control-plane"),):
    ps = [p for p in final_pods if p["metadata"].get("labels", {}).get("job-name") == label]
    if not ps or any(p["spec"].get("nodeName") != want for p in ps):
        invalid.append("k6 Pod not on %s" % want)
pr = [p for p in final_pods if p["metadata"]["name"] == "prober"]
if mode != "capacity" and (not pr or pr[0]["spec"].get("nodeName") != CLUSTER + "-control-plane"):
    invalid.append("prober Pod not on %s-control-plane" % CLUSTER)
if any("error" in x for x in pods_raw):
    invalid.append("status poll errors: %d" % sum(1 for x in pods_raw if "error" in x))
if probe_scrapes == 0 or probe_scrape_empty:
    invalid.append("kubelet probe metrics missing in %d of %d scrapes" % (probe_scrape_empty, probe_scrapes))
if watch["errors"]:
    invalid.append("event watch errors: %s" % watch["errors"][:3])
if gaps:
    invalid.append("event watch gaps: %s" % gaps[:5])
if eps_watch_log["errors"]:
    invalid.append("EndpointSlice watch errors: %s" % eps_watch_log["errors"][:3])
if mode != "capacity":
    if any(not probes.get(n) for n in names):
        invalid.append("no prober records for some Pods")
    if any(not stats.get(n) for n in names):
        invalid.append("no stat lines for some Pods")

rate_per_pod = None
rate_by_pod = {}
expected = capacity(server)
if mode != "capacity":
    done_total = busy_total = 0
    for n in names:
        if server == "gunicorn":
            secs = {int(t // 1) for t, q in listen.get(n, []) if 0 <= t < OVER_SECONDS and q > 0}
        else:
            secs = {int(r["to"] // 1) for r in stats.get(n, []) if 0 <= r["to"] < OVER_SECONDS and r["waiting"] > 0}
        cnt = sum(r["done"] for r in stats.get(n, []) if int(r["to"] // 1) in secs and 0 <= r["to"] < OVER_SECONDS)
        rate_by_pod[short[n]] = {"busy_seconds": len(secs), "processed": cnt, "rps": round(cnt / len(secs), 1) if secs else None}
        done_total += cnt
        busy_total += len(secs)
    rate_per_pod = round(done_total / busy_total, 1) if busy_total else None
    if rate_per_pod is None or abs(rate_per_pod - expected) >= 0.05 * expected:
        invalid.append("processing rate %s rps per Pod in the seconds of the 1.2C stage with requests waiting is 5%% or more "
                       "away from the local measurement %.1f rps (scripts/capacity.env)" % (rate_per_pod, expected))

cap = None
if mode == "capacity":
    ok = collections.Counter(int(t + d) for t, d, cls, e in reqs if cls == "ok")
    ks = sorted(ok)[10:-2]
    total = round(sum(ok[k] for k in ks) / len(ks), 1) if ks else None
    cap = {"vus": 60, "rps_3_pods": total, "rps_per_pod": round(total / PODS, 1) if total else None, "work_ms": work_ms(server),
           "errors": {k: v for k, v in counts.items() if k != "ok"}}

pred = None
if rate_per_pod:
    r = rate_per_pod
    arrive = 1.2 * C / PODS
    ex = arrive - r
    pred = {"rate_per_pod": r, "arrival_per_pod": arrive, "excess_per_pod": round(ex, 1)}
    if server == "gunicorn":
        pred["probe_wait_over_1s_t"] = round(r / ex, 1) if ex > 0 else None
    if server == "tomcat":
        pred["busy_200_t"] = round(196 / ex, 1) if ex > 0 else None
        pred["probe_wait_over_1s_t"] = round((196 + r) / ex, 1) if ex > 0 else None
    if server == "go":
        base = med(list(server_metrics["goroutines_before"].values()))
        pred["goroutines_at_overload_end"] = round(ex * OVER_SECONDS + arrive * 2 + (base or 0))
        pred["goroutines_parts"] = {"waiting": round(ex * OVER_SECONDS), "connected": arrive * 2, "base": base}

metrics = {
    "server": server, "mode": mode, "name": SERVERS[server]["name"], "C": C, "stages": st, "work_ms": work_ms(server),
    "expected_rps_per_pod": expected, "invalid": invalid, "dropped": dropped, "achieved": achieved,
    "pods": {short[n]: n for n in names}, "restart_count": {short[n]: final_rc.get(n) for n in names},
    "kills": [{"pod": short[k["name"]], "t": k["t"]} for k in kills],
    "probe_first_over_1s": probe_first_over, "probe_first_over_1s_min": min(firsts) if firsts else None,
    "probe_max": probe_max, "probe_max_t": probe_max_t, "probe_max_in_1.2C": probe_max_over, "probe_max_before": probe_max_before, "probe_results": dict(probe_results),
    "cpu_cores_max_in_1.2C": cpu_max_over, "cpu_cores_median_in_1.2C": cpu_med_over, "host_cpu_busy": host_busy_over, "probe_spikes": probe_spikes, "probe_windows": probe_windows, "window": win, "refused_split": dict(refused_split), "readiness": readiness, "recvq_max": recvq_max, "recvq_max_before_first_kill": recvq_max_before_kill, "listen_overflows": overflows, "fd_before": fd_base, "fd_max": fd_max, "fd_max_t": fd_max_t,
    "server_metrics": server_metrics, "k6": dict(counts), "k6_error_samples": err_samples,
    "restart_windows": restart_windows, "ready_zero_seconds": round(zero_seconds, 1), "first_unready": first_unready, "ready_min": min_ready,
    "events": dict(event_counts), "event_list": [e for e in ev_occ if e["name"] in short and e["t"] >= -WARM],
    "kubelet_probes": dict(kubelet_probes), "rate_per_pod": rate_per_pod, "rate_by_pod": rate_by_pod,
    "capacity": cap, "prediction": pred,
    "k6_by_second": {str(k): dict(v) for k, v in sorted(by_sec.items())},
}
json.dump(metrics, open(P("metrics.json"), "w"), indent=1, default=list)

print("# %s %s (%s)" % (server, mode, "INVALID" if invalid else "valid"))
print()
for i in invalid:
    print("RUN INVALID: " + i)
if invalid:
    print()
if cap:
    print("- closed loop, 60 VUs, WORK_MS=%s: %s rps for 3 Pods, %s rps per Pod, errors %s" % (
        cap["work_ms"], cap["rps_3_pods"], cap["rps_per_pod"], cap["errors"]))
    sys.exit(0)
print("C = %d rps. Stages: %s. WORK_MS=%s. t = 0 is the start of the 1.2C stage." % (
    C, ", ".join("%d rps for %d s" % (r, s) for r, s, _ in st), work_ms(server)))
print()
for k in ("probe_first_over_1s", "probe_max_before", "probe_max", "probe_max_t", "probe_max_in_1.2C", "probe_results", "restart_count", "kills", "cpu_cores_max_in_1.2C", "cpu_cores_median_in_1.2C", "host_cpu_busy", "probe_spikes", "probe_windows", "window", "refused_split", "readiness", "recvq_max", "recvq_max_before_first_kill",
          "listen_overflows", "fd_before", "fd_max", "fd_max_t", "server_metrics", "k6", "k6_error_samples", "restart_windows",
          "first_unready", "ready_zero_seconds", "ready_min", "events", "kubelet_probes", "rate_per_pod", "rate_by_pod", "achieved", "dropped",
          "prediction"):
    print("- %s: %s" % (k, json.dumps(metrics[k], default=list)))
