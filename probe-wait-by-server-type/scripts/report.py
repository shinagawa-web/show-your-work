import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from servers import ORDER, SERVERS

runs = {}
for d in sys.argv[1:]:
    p = os.path.join(d, "metrics.json")
    if os.path.exists(p):
        m = json.load(open(p))
        if m.get("mode") == "run":
            runs[m["server"]] = m
SECTIONS = "1, 3, 4"


def job(s):
    return "%s (%s)" % (SECTIONS, s)


def g(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        return ("%d" % v) if v.is_integer() else ("%." + str(nd) + "f") % v
    return str(v)


def per_pod(d, nd=2):
    return ", ".join(g(d[k], nd) for k in sorted(d)) if d else "-"


def maxof(d):
    v = [x for x in (d or {}).values() if x is not None]
    return max(v) if v else None


def minof(d):
    v = [x for x in (d or {}).values() if x is not None]
    return min(v) if v else None


def present():
    return [s for s in ORDER if s in runs]


def failures(m):
    k = {c: n for c, n in m["k6"].items() if c != "ok"}
    return ", ".join("%s %d" % (c, k[c]) for c in sorted(k)) or "none"


def restarted(m):
    return sum(v or 0 for v in m["restart_count"].values())


def window(m, label):
    w = m.get("probe_windows") or {}
    out = []
    for k in sorted(w):
        x = w[k].get(label)
        out.append("-" if not x else "%s s at t=%s (stat interval %s s)" % (g(x["wait"], 3), g(x["t"], 1), g(x["stat_interval_max"])))
    return "; ".join(out)


def readiness(m):
    r = m.get("readiness") or {}
    return "; ".join("%s total, %s from t=-15, at most %s in a row from t=-15" % (
        r[k]["failed_total"], r[k]["failed_from_t-15"], r[k]["max_consecutive_failed_from_t-15"]) for k in sorted(r))


def refused(m):
    r = m.get("refused_split") or {}
    keys = ["from a liveness Killing to the Pod not Ready", "while 0 endpoints were Ready", "other"]
    return ", ".join("%s %d" % (k, r.get(k, 0)) for k in keys)


print("# probe-wait-by-server-type")
print()
print("One run per server. t = 0 is the start of the 1.2C stage (720 rps for 60 s, after 450 rps for 15 s, then 450 rps "
      "for 105 s). The probe wait is from the separate prober "
      "(a new connection to each Pod IP every second). The numbers for each server come from the job "
      "`%s (<server>)`." % SECTIONS)
print()
invalid = [s for s in present() if runs[s]["invalid"]]
if invalid:
    for s in invalid:
        print("RUN INVALID in `%s`: %s" % (job(s), "; ".join(runs[s]["invalid"])))
    print()
missing = [s for s in ORDER if s not in runs]
if missing:
    print("No results for: %s" % ", ".join("`%s`" % job(s) for s in missing))
    print()

def restart_windows(m):
    w = m["restart_windows"]
    if not w:
        return "no restarts"
    return "; ".join("Pod %s at t=%s: reset %d, eof %d" % (x["pod"], g(x["kill_t"], 1), x["reset_within_2s"], x["eof_within_2s"])
                     for x in w)


def over_1s(m):
    sp = collections.defaultdict(list)
    for x in m.get("probe_spikes") or []:
        if x["wait"] > 1.0:
            sp[x["pod"]].append(x["t"])
    out = []
    for k in sorted(m["probe_first_over_1s"]):
        ts_ = sorted(sp.get(k, []))
        best = run = 0
        prev = None
        for t in ts_:
            run = run + 1 if prev is not None and t - prev < 1.5 else 1
            best = max(best, run)
            prev = t
        out.append("%d (at most %d in a row)" % (len(ts_), best))
    return ", ".join(out)


def lines(s, items, title=True):
    if title:
        print("### %s" % SERVERS[s]["name"])
        print()
    for label, v in items:
        print("- %s: %s" % (label, v))
    print()


def W(m, key):
    return (m.get("window") or {}).get(key) or {}


def first_over_win(m):
    d = W(m, "probe_first_over_1s")
    return ", ".join("never" if d[k] is None else g(d[k], 1) for k in sorted(d))


def period(m):
    e = W(m, "end")
    return ", ".join("0 to %s" % g(e[k], 1) for k in sorted(e))


def table(title, head, rows):
    print(title)
    print()
    print("| " + " | ".join(head) + " |")
    print("|" + "---|" * len(head))
    for r in rows:
        print("| " + " | ".join(r) + " |")
    print()


print("## 1. Opening")
print()
table("Which servers the default probes restarted.", ["Server", "Restarted"],
      [[SERVERS[s]["name"], "yes" if restarted(m) else "no"] for s, m in ((s, runs[s]) for s in present())])

print("## 3. Results by server")
print()
print("The values in this section and in section 4 are from t = 0 to 60 s (the 1.2C stage). For a Pod that was restarted, "
      "they end at its first liveness Killing Event.")
print()
def over_timeout(m):
    end = W(m, "end")
    hits = collections.Counter(x["pod"] for x in m.get("probe_spikes") or [] if x["wait"] > 1.0 and x["t"] < end.get(x["pod"], 0))
    if not hits:
        return "no"
    if len(hits) == len(end):
        return "yes"
    n = sum(hits.values())
    return "yes, %d of %d Pods, %s" % (len(hits), len(end), {1: "once", 2: "twice"}.get(n, "%d times" % n))


table("Whether the probe waited longer than 1 s, the default `timeoutSeconds` (the separate prober, one probe per Pod per second).",
      ["Server", "Probe wait over 1 s"],
      [[SERVERS[s]["name"], over_timeout(runs[s])] for s in present()])
table("Requests waiting in the kernel accept queue of the listen socket (Recv-Q).",
      ["Server", "Recv-Q max over the 3 Pods"],
      [[SERVERS[s]["name"], g(maxof(W(runs[s], "recvq_max")))] for s in present()])


def k6n(m, c):
    return str(m["k6"].get(c, 0))


table("What the client saw: k6 requests by result over the whole run (t = -15 s to the end of k6). `timeout` is the 2 s client "
      "timeout (whether any occurred), `reset` is connection reset by peer, `eof` is the connection closed with no response, "
      "`refused` is connection refused (counts).",
      ["Server", "timeout", "reset", "eof", "refused"],
      [[SERVERS[s]["name"], "yes" if runs[s]["k6"].get("timeout") else "no", k6n(runs[s], "reset"), k6n(runs[s], "eof"),
        k6n(runs[s], "refused")] for s in present()])


print("The value that moved with the waiting, per server:")
print()
if "tomcat" in runs:
    lines("tomcat", [("`tomcat.threads.busy` max over the 3 Pods", g(maxof(W(runs["tomcat"], "busy_max")))),
                     ("`tomcat.connections.current` max over the 3 Pods", g(maxof(W(runs["tomcat"], "conns_max"))))])
if "tomcat-virtual" in runs:
    lines("tomcat-virtual", [("`tomcat.connections.current` max over the 3 Pods", g(maxof(W(runs["tomcat-virtual"], "conns_max"))))])
if "go" in runs:
    lines("go", [("goroutines max over the 3 Pods", g(maxof(W(runs["go"], "goroutines_max"))))])

print("## 4. Open file descriptors")
print()
table("Open file descriptors per Pod, counted from the node in `/proc/<pid>/fd` of every process in the container every second. "
      "Before is the median of each Pod from t = -15 to 0 s, the largest of the 3 Pods; max is over the period of section 3 and the 3 Pods.",
      ["Server", "Before (largest of the 3 Pod medians)", "Max over the 3 Pods"],
      [[SERVERS[s]["name"], g(maxof(runs[s]["fd_before"])), g(maxof(W(runs[s], "fd_max")))] for s in present()])

print("## Numbers not used")
print()
print("Local capacity measurement and sleep time per server (`scripts/capacity.env`, as recorded in each run):")
print()
print("| Server | WORK_MS | Per-Pod rate measured locally (rps) |")
print("|---|---|---|")
for s in present():
    print("| %s | %s | %s |" % (SERVERS[s]["name"], runs[s]["work_ms"], g(runs[s]["expected_rps_per_pod"], 1)))
print()
print("| Job | Rate per Pod while requests waited (rps) | Invalid | Dropped iterations | Achieved rps per stage | Seconds with 0 Ready endpoints | Ready endpoints min | kubelet `prober_probe_total` | Events |")
print("|---|---|---|---|---|---|---|---|---|")
for s in present():
    m = runs[s]
    print("| `%s` | %s | %s | %d | %s | %s | %s | %s | %s |" % (
        job(s), g(m["rate_per_pod"], 1), "; ".join(m["invalid"]) or "no", m["dropped"],
        " / ".join(g(a["achieved_rps"], 1) for a in m["achieved"]),
        g(m["ready_zero_seconds"], 1), g(m["ready_min"]),
        ", ".join("%s %d" % (k, v) for k, v in sorted(m["kubelet_probes"].items())),
        ", ".join("%s ×%d" % (k, v) for k, v in sorted(m["events"].items()))))
print()
print("Values per server that are not in sections 1 to 4. Values per Pod are listed in Pod name order. \"Whole load\" is t = 0 to 165 s, including restarted containers; "
      "a whole-load line is listed only where it differs from the period of section 3, and \"same\" marks a Pod whose value is the one "
      "for the period of section 3.")
print()


def pods_excl(d, shown, label, nd=2):
    return ", ".join(label if d[k] == shown else g(d[k], nd) for k in sorted(d))


def whole(m, win_key, vals, nd=2, fmt=None):
    w = W(m, win_key)
    if all(w.get(k) == vals.get(k) for k in vals):
        return None
    f = fmt or (lambda k: g(vals[k], nd))
    return ", ".join("same" if w.get(k) == vals[k] else f(k) for k in sorted(vals))


def never(v):
    return "never" if v is None else g(v, 1)


for s in present():
    m = runs[s]
    sm = m["server_metrics"]
    cand = [("probe wait first over 1 s per Pod, period of section 3 (t, s)", first_over_win(m)),
            ("probe wait max per Pod, period of section 3 (s)", per_pod(W(m, "probe_max"), 3)),
            ("probe wait first over 1 s per Pod, whole load (t, s)",
             whole(m, "probe_first_over_1s", m["probe_first_over_1s"], fmt=lambda k: never(m["probe_first_over_1s"][k])))]
    if s in ("gunicorn", "tomcat"):
        cand.append(("probe wait max per Pod, whole load (s, at t)",
                     whole(m, "probe_max", m["probe_max"], fmt=lambda k: "%s (t=%s)" % (g(m["probe_max"][k], 3), g(m["probe_max_t"][k], 1)))))
    if s in ("tomcat-virtual", "node"):
        aft = {k: (v.get("after") or {}) for k, v in (m.get("probe_windows") or {}).items()}
        cand.append(("probe wait max per Pod after the 1.2C stage (t = 60 to 165 s), outside the period of section 3",
                     ", ".join("in the list below" if (aft[k].get("wait") or 0) > 0.05 else g(aft[k].get("wait"), 3) for k in sorted(aft))))
    rq = W(m, "recvq_max")
    cand.append(("Recv-Q max per Pod, period of section 3", pods_excl(rq, maxof(rq), "the max in section 3")))
    cand.append(("Recv-Q max per Pod, whole load", whole(m, "recvq_max", m["recvq_max"])))
    if s == "tomcat":
        cand.append(("executor queue max per Pod, period of section 3", per_pod(W(m, "queue_max"))))
        cand.append(("executor queue max per Pod, whole load", whole(m, "queue_max", sm["queue_max"])))
    fdw = W(m, "fd_max")
    cand.append(("open file descriptors before the 1.2C stage per Pod (median from t = -15 to 0 s)",
                 pods_excl(m["fd_before"], maxof(m["fd_before"]), "the value in section 4")))
    cand.append(("open file descriptors max per Pod, period of section 3", pods_excl(fdw, maxof(fdw), "the max in section 4")))
    cand.append(("open file descriptors max per Pod, whole load", whole(m, "fd_max", m["fd_max"])))
    cand.append(("k6 timeout count, whole run", str(m["k6"].get("timeout", 0))))
    for key, name in (("busy_max", "`tomcat.threads.busy`"), ("conns_max", "`tomcat.connections.current`"), ("goroutines_max", "goroutines")):
        if (s == "tomcat" and key in ("busy_max", "conns_max")) or (s == "tomcat-virtual" and key == "conns_max") or (s == "go" and key == "goroutines_max"):
            d = W(m, key)
            cand.append(("%s max per Pod, period of section 3" % name, pods_excl(d, maxof(d), "the max in section 3")))
    if s == "tomcat":
        cand.append(("`tomcat.threads.busy` first at 200 per Pod, period of section 3 (t, s)", per_pod(W(m, "busy_first_200"), 1)))
    if s == "gunicorn":
        cand.append(("ListenOverflows increase per Pod, whole run", per_pod(m["listen_overflows"])))
    if s in ("gunicorn", "tomcat"):
        cand += [("k6 reset / eof from 1 s before to 2 s after each liveness Killing Event", restart_windows(m)),
                 ("k6 refused, by when it ended", refused(m))]
    if s == "tomcat-virtual":
        cand += [("`tomcat.threads.busy` values seen per Pod, whole run", ", ".join("/".join(g(x) for x in v) for k, v in sorted(sm["busy_values"].items()))),
                 ("probe waits over 1 s per Pod, whole load", over_1s(m))]
    if s in ("tomcat-virtual", "node"):
        cand.append(("kubelet `prober_probe_total` Readiness failed per Pod", readiness(m)))
    if s == "node":
        cand.append(("`/work` requests in the handler (waiting and running) max per Pod, period of section 3", per_pod(W(m, "inflight_max"))))
    if s in ("node", "go"):
        cand.append(("open connections max per Pod (`conns` in the `stat` lines), period of section 3", per_pod(W(m, "conns_max"))))
    items = [(k, v) for k, v in cand if v is not None]
    print("`%s`:" % job(s))
    print()
    lines(s, items, title=False)
if "node" in runs:
    sm = runs["node"]["server_metrics"]
    same = sm["lag_p99_ms_max"] == sm["lag_p99_ms_max_in_1.2C"]
    print("Node.js event loop delay in `%s` (`monitorEventLoopDelay`, resolution 10 ms, p99 of each second): max in the 1.2C stage per Pod %s ms%s; "
          "median before the 1.2C stage %s ms" % (
              job("node"), per_pod(sm["lag_p99_ms_max_in_1.2C"]),
              " (also the max over the run)" if same else "; max over the run %s ms" % per_pod(sm["lag_p99_ms_max"]),
              per_pod(sm["lag_p99_ms_before"])))
    print()
print("CPU in the 1.2C stage: busy share of the VM from `/proc/stat` on the worker node (median, max) and cores used per Pod from its cgroup (median, max):")
print()
for s in present():
    m = runs[s]
    h = m.get("host_cpu_busy") or {}
    print("- `%s`: VM %s, %s (before the 1.2C stage %s); Pods %s / %s" % (
        job(s), g(h.get("median")), g(h.get("max")), g(h.get("before")),
        per_pod(m.get("cpu_cores_median_in_1.2C") or {}), per_pod(m.get("cpu_cores_max_in_1.2C") or {})))
print()
print("Probe waits over 0.05 s during the load on the servers without a limit, with the VM CPU busy share at most from 0.5 s before to "
      "1.5 s after, and the longest interval between two `stat` lines of that Pod (printed once a second by a thread that only sleeps) "
      "overlapping the probe:")
print()
def spike(m, x):
    wmax = W(m, "probe_max").get(x["pod"])
    if x["t"] < 60 and wmax == x["wait"]:
        wait = "the max for the period of section 3, listed above"
    elif x["t"] >= 60:
        wait = "%s s, after the 1.2C stage, not in section 3" % g(x["wait"], 3)
    else:
        wait = "%s s" % g(x["wait"], 3)
    return "t=%s Pod %s %s (VM %s, stat %s s)" % (g(x["t"], 1), x["pod"], wait, g(x["host_cpu_busy"]), g(x.get("stat_interval_max")))


for s in [x for x in present() if x in ("tomcat-virtual", "node", "go")]:
    sp = runs[s].get("probe_spikes") or []
    print("- `%s`: %s" % (job(s), "; ".join(spike(runs[s], x) for x in sp) or "none"))
print()
print("Predictions recomputed from the rate per Pod of each run:")
print()
for s in present():
    print("- `%s`: %s" % (job(s), json.dumps(runs[s]["prediction"])))
