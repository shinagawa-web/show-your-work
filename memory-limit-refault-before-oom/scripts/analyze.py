import csv
import json
import os
import sys

MIB = 2**20
TIMEOUT_MS = 5000.0
STOP_PGMAJFAULT_PER_S = 100


def meta(d):
    out = {}
    for line in open(os.path.join(d, "meta.txt")):
        k, _, v = line.rstrip("\n").partition("=")
        out[k] = v
    return out


def samples(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def requests(d):
    rows = []
    for r in csv.DictReader(open(os.path.join(d, "requests.csv"))):
        sched = int(r["sched_unix_ns"]) / 1e9
        recv = int(r["recv_unix_ns"]) / 1e9
        to = r["timeout"] == "true"
        lat = TIMEOUT_MS if to else min((recv - sched) * 1000, TIMEOUT_MS)
        rows.append({"sched": sched, "recv": recv, "lat": lat, "timeout": to, "ok": not r["err"]})
    return rows


def pct(vals, q):
    v = sorted(vals)
    if not v:
        return None
    return v[min(len(v) - 1, int(q * len(v)))]


def med(vals):
    return pct(vals, 0.5)


def r2(x, n=2):
    return None if x is None else round(x, n)


def headroom(s):
    return (s["max"] - (s["current"] - s["stat"]["file"])) / MIB


def per_second(reqs, t0, n_s):
    out = []
    for k in range(n_s):
        lo, hi = t0 + k, t0 + k + 1
        win = [r for r in reqs if lo <= r["sched"] < hi]
        out.append({"s": k, "p99_ms": r2(pct([r["lat"] for r in win], 0.99), 1), "timeouts": sum(r["timeout"] for r in win),
                    "completed": sum(1 for r in reqs if lo <= r["recv"] < hi and r["ok"])})
    return out


def analyze(d):
    m = meta(d)
    reqs = requests(d)
    t0 = reqs[0]["sched"]
    t_end = reqs[-1]["sched"]
    S = samples(os.path.join(d, "cgroup.jsonl"))
    alive = [s for s in S if s["alive"]]
    win = [s for s in alive if t0 + 1 <= s["t"] <= t_end + 0.5]
    o = {"label": m["label"], "mode": m["mode"], "memory_mib": float(m["memory_mib"]), "cache_mib": float(m["cache_mib"]), "elem_kb": int(m["elem_kb"]),
         "rps": float(m["rps"]), "dur_s": int(m["dur_s"]), "grow_kbps": float(m["grow_kbps"]), "grow_per_requests": int(m.get("grow_per_requests", 0) or 0),
         "kernel": m.get("kernel"), "lru_gen_enabled": m["lru_gen_enabled"],
         "n_requests": len(reqs), "timeouts": sum(r["timeout"] for r in reqs), "p99_ms": r2(pct([r["lat"] for r in reqs], 0.99), 1)}
    if win:
        a, b = win[0], win[-1]
        dt = b["t"] - a["t"]
        o["limit_mib"] = r2(a["max"] / MIB)
        o["pgmajfault_per_s"] = r2((b["stat"]["pgmajfault"] - a["stat"]["pgmajfault"]) / dt, 1) if dt else None
        o["mem_psi_some_s_per_s"] = r2((b["mem_psi"]["some"] - a["mem_psi"]["some"]) / dt / 1e6, 4) if dt else None
        o["headroom_mib"] = r2(med([headroom(s) for s in win]))
        o["anon_mib"] = r2(med([s["stat"]["anon"] / MIB for s in win]))
        o["file_mib"] = r2(med([s["stat"]["file"] / MIB for s in win]))
        o["file_mapped_mib"] = r2(med([s["stat"]["file_mapped"] / MIB for s in win]))
        o["usage_pct_of_limit"] = r2(med([s["current"] / s["max"] * 100 for s in win]), 1)
        o["anon_pct_of_limit"] = r2(med([s["stat"]["anon"] / s["max"] * 100 for s in win]), 1)
    if alive:
        o["swap"] = {"max": alive[0].get("swap_max"), "current_max": max(s.get("swap_current") or 0 for s in alive)}
    pe0 = S[0]["parent_events"].get("oom_kill", 0) if S else 0
    end = samples(os.path.join(d, "cgroup_end.jsonl"))
    pe1 = (end[-1] if end else S[-1])["parent_events"].get("oom_kill", 0)
    insp = dict(x.split("=", 1) for x in open(os.path.join(d, "inspect.txt")).read().split())
    o["oom"] = {"container_oom_kill": alive[-1]["events"].get("oom_kill", 0) if alive else None, "parent_oom_kill_delta": pe1 - pe0,
                "docker_oom_killed": insp.get("oom_killed"), "exit_code": insp.get("exit_code")}
    o["oom_killed"] = o["oom"]["parent_oom_kill_delta"] > 0 or insp.get("oom_killed") == "true"
    info = os.path.join(d, "info.json")
    if os.path.exists(info):
        o["app"] = json.load(open(info))
    sec = per_second(reqs, t0, o["dur_s"])
    if m["mode"] in ("p3", "p3b"):
        o["growth"] = growth(d, reqs, t0, S, sec, m)
    json.dump(sec, open(os.path.join(d, "per_second.json"), "w"), indent=1)
    json.dump(o, open(os.path.join(d, "summary.json"), "w"), indent=1)
    return o


def growth(d, reqs, t0, S, sec, m):
    k0 = S[0]["parent_events"].get("oom_kill", 0) if S else 0
    oom_t = next((s["t"] - t0 for s in S if s["parent_events"].get("oom_kill", 0) > k0), None)
    alive = [s for s in S if s["alive"]]
    by_s = {}
    for a, b in zip(alive, alive[1:]):
        k = int(b["t"] - t0)
        if b["t"] < t0 or k in by_s:
            continue
        dt = b["t"] - a["t"]
        by_s[k] = {"anon_mib": r2(b["stat"]["anon"] / MIB, 1), "file_mib": r2(b["stat"]["file"] / MIB, 1), "file_mapped_mib": r2(b["stat"]["file_mapped"] / MIB, 2),
                   "headroom_mib": r2(headroom(b), 2), "usage_pct_of_limit": r2(b["current"] / b["max"] * 100, 1), "anon_pct_of_limit": r2(b["stat"]["anon"] / b["max"] * 100, 1),
                   "pgmajfault_per_s": r2((b["stat"]["pgmajfault"] - a["stat"]["pgmajfault"]) / dt, 1),
                   "mem_psi_some_s_per_s": r2((b["mem_psi"]["some"] - a["mem_psi"]["some"]) / dt / 1e6, 4),
                   "oom_kill": b["parent_events"].get("oom_kill", 0) - k0}
    series = []
    for x in sec:
        row = {"t_s": x["s"], "completed": x["completed"], "p99_ms": x["p99_ms"], "timeouts": x["timeouts"]}
        row.update(by_s.get(x["s"], {"oom_kill": 1 if oom_t is not None and x["s"] >= oom_t else None}))
        series.append(row)
    edge = next((r for r in series if (r.get("pgmajfault_per_s") or 0) > STOP_PGMAJFAULT_PER_S), None)
    stalled = [r["mem_psi_some_s_per_s"] for r in series if edge and r["t_s"] >= edge["t_s"] and r.get("mem_psi_some_s_per_s") is not None]
    grows = []
    gp = os.path.join(d, "grow.log")
    if os.path.exists(gp):
        for line in open(gp):
            f = dict(x.split("=") for x in line.split()[1:])
            grows.append((r2(float(f["t"]) - t0, 3), int(f["elements"])))
    json.dump(grows, open(os.path.join(d, "grow.json"), "w"))
    windows = {}
    if edge:
        k = edge["t_s"]
        for name, lo, hi in (("before_stop", k - 5, k), ("after_stop", k, k + 10)):
            rows = [r for r in series if lo <= r["t_s"] < hi and r.get("anon_mib") is not None]
            lat = [q["lat"] for q in reqs if t0 + lo <= q["sched"] < t0 + hi]
            windows[name] = {"from_s": lo, "to_s": hi, "p99_ms": r2(pct(lat, 0.99), 1),
                             "usage_pct_of_limit": r2(med([r["usage_pct_of_limit"] for r in rows]), 1) if rows else None,
                             "anon_pct_of_limit": r2(med([r["anon_pct_of_limit"] for r in rows]), 1) if rows else None,
                             "pgmajfault_per_s": r2(sum(r["pgmajfault_per_s"] for r in rows) / len(rows), 1) if rows else None}
    return {"stop_s": edge["t_s"] if edge else None, "headroom_at_stop_mib": edge.get("headroom_mib") if edge else None,
            "file_mapped_at_stop_mib": edge.get("file_mapped_mib") if edge else None, "anon_at_stop_mib": edge.get("anon_mib") if edge else None,
            "mem_psi_some_median_while_stalled": r2(med(stalled), 4) if stalled else None,
            "oom_kill_at_s": r2(oom_t, 1), "load_s": int(m["dur_s"]),
            "appends": len(grows), "elements_at_stop": next((n for t, n in reversed(grows) if edge and t < edge["t_s"] + 1), None),
            "appends_after_stop": sum(1 for t, _ in grows if t >= edge["t_s"] + 1) if edge else None,
            "windows": windows, "series_1s": series}


if __name__ == "__main__":
    for d in sys.argv[1:]:
        o = analyze(d)
        print(json.dumps({k: o.get(k) for k in ("label", "p99_ms", "timeouts", "headroom_mib", "pgmajfault_per_s", "oom_killed")}))
