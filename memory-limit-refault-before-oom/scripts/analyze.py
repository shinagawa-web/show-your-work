import csv
import json
import os
import sys

MIB = 2**20
TIMEOUT_MS = 5000.0


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
        to = r["timeout"] == "true"
        lat = TIMEOUT_MS if to else min((int(r["recv_unix_ns"]) / 1e9 - sched) * 1000, TIMEOUT_MS)
        rows.append({"sched": sched, "recv": int(r["recv_unix_ns"]) / 1e9, "lat": lat, "timeout": to, "err": bool(r["err"]) and not to, "status": int(r["status"])})
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


def slope(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else None


def headroom(s):
    return (s["max"] - (s["current"] - s["stat"]["file"])) / MIB


def rates(a, b):
    dt = b["t"] - a["t"]
    st = lambda k: (b["stat"].get(k, 0) - a["stat"].get(k, 0)) / dt
    dmaj = b["stat"].get("pgmajfault", 0) - a["stat"].get("pgmajfault", 0)
    return {
        "workingset_refault_file_per_pgmajfault": r2((b["stat"].get("workingset_refault_file", 0) - a["stat"].get("workingset_refault_file", 0)) / dmaj, 2) if dmaj else None,
        "io_psi_some_us_per_pgmajfault": r2((b["io_psi"]["some"] - a["io_psi"]["some"]) / dmaj, 1) if dmaj else None,
        "dt_s": r2(dt),
        "pgmajfault_per_s": r2(st("pgmajfault"), 1),
        "pgscan_direct_per_s": r2(st("pgscan_direct"), 1),
        "pgsteal_direct_per_s": r2(st("pgsteal_direct"), 1),
        "workingset_refault_file_per_s": r2(st("workingset_refault_file"), 1),
        "events_max_per_s": r2((b["events"].get("max", 0) - a["events"].get("max", 0)) / dt, 1),
        "mem_psi_some_s_per_s": r2((b["mem_psi"]["some"] - a["mem_psi"]["some"]) / dt / 1e6, 4),
        "mem_psi_full_s_per_s": r2((b["mem_psi"]["full"] - a["mem_psi"]["full"]) / dt / 1e6, 4),
        "io_psi_some_s_per_s": r2((b["io_psi"]["some"] - a["io_psi"]["some"]) / dt / 1e6, 4),
        "cpu_psi_some_s_per_s": r2((b["cpu_psi"]["some"] - a["cpu_psi"]["some"]) / dt / 1e6, 4),
        "cpu_user_s_per_s": r2((b["cpu"]["user_usec"] - a["cpu"]["user_usec"]) / dt / 1e6, 4),
        "cpu_system_s_per_s": r2((b["cpu"]["system_usec"] - a["cpu"]["system_usec"]) / dt / 1e6, 4),
    }


def per_second(reqs, t0, n_s, rps):
    out = []
    for k in range(n_s):
        lo, hi = t0 + k, t0 + k + 1
        win = [r for r in reqs if lo <= r["sched"] < hi]
        done = sum(1 for r in reqs if lo <= r["recv"] < hi and not r["timeout"] and not r["err"])
        inflight = sum(1 for r in reqs if r["sched"] < hi and r["recv"] >= hi)
        out.append({"s": k, "n": len(win), "p99_ms": r2(pct([r["lat"] for r in win], 0.99), 1), "timeouts": sum(r["timeout"] for r in win), "completed": done, "inflight_at_end": inflight})
    return out


def analyze(d):
    m = meta(d)
    rps = float(m["rps"])
    reqs = requests(d)
    t0 = reqs[0]["sched"]
    t_end = reqs[-1]["sched"]
    S = samples(os.path.join(d, "cgroup.jsonl"))
    alive = [s for s in S if s["alive"]]
    win = [s for s in alive if t0 + 1 <= s["t"] <= t_end + 0.5]
    pre = [s for s in alive if s["t"] < t0]
    lats = [r["lat"] for r in reqs]
    o = {"dir": d, "label": m["label"], "mode": m["mode"], "memory_mib": float(m["memory_mib"]), "memory_bytes": int(m["memory_bytes"]),
         "cache_mib": float(m["cache_mib"]), "elem_kb": int(m["elem_kb"]), "rps": rps, "dur_s": int(m["dur_s"]), "lru_gen_enabled": m["lru_gen_enabled"], "read_ahead_kb": m.get("read_ahead_kb"), "kernel": m.get("kernel"),
         "grow_kbps": float(m["grow_kbps"]),
         "n_requests": len(reqs), "timeouts": sum(r["timeout"] for r in reqs), "errors": sum(r["err"] for r in reqs),
         "latency_ms": {"p50": r2(pct(lats, 0.5), 1), "p99": r2(pct(lats, 0.99), 1), "max": r2(max(lats), 1)}}
    if win:
        a, b = win[0], win[-1]
        lim = a["max"]
        o["window"] = rates(a, b) if b is not a else None
        o["headroom_mib"] = {"median": r2(med([headroom(s) for s in win])), "min": r2(min(headroom(s) for s in win)), "max": r2(max(headroom(s) for s in win))}
        nonfile = [(s["current"] - s["stat"]["file"]) / MIB for s in win]
        o["nonfile_mib"] = {"median": r2(med(nonfile)), "swing": r2(max(nonfile) - min(nonfile), 3)}
        o["anon_mib"] = r2(med([s["stat"]["anon"] / MIB for s in win]))
        o["file_mib"] = r2(med([s["stat"]["file"] / MIB for s in win]))
        o["file_mapped_mib"] = r2(med([s["stat"]["file_mapped"] / MIB for s in win]))
        o["anon_pct_of_limit"] = r2(med([s["stat"]["anon"] / s["max"] * 100 for s in win]), 1)
        o["file_pct_of_limit"] = r2(med([s["stat"]["file"] / s["max"] * 100 for s in win]), 1)
        o["usage_pct_of_limit"] = r2(med([s["current"] / s["max"] * 100 for s in win]), 1)
        o["anon_slope_mib_per_s"] = r2(slope([s["t"] for s in win], [s["stat"]["anon"] / MIB for s in win]), 4)
        o["swap"] = {"max": a.get("swap_max"), "current_max": max(s.get("swap_current") or 0 for s in win)}
        o["limit_mib"] = r2(lim / MIB)
    if pre:
        p = pre[-1]
        o["at_start"] = {"file_mib": r2(p["stat"]["file"] / MIB), "file_mapped_mib": r2(p["stat"]["file_mapped"] / MIB), "anon_mib": r2(p["stat"]["anon"] / MIB), "headroom_mib": r2(headroom(p))}
        if len(pre) >= 2:
            o["at_start"]["pgmajfault_per_s"] = r2((pre[-1]["stat"]["pgmajfault"] - pre[0]["stat"]["pgmajfault"]) / (pre[-1]["t"] - pre[0]["t"]), 1)
    pe0 = S[0]["parent_events"].get("oom_kill", 0) if S else 0
    end = samples(os.path.join(d, "cgroup_end.jsonl"))
    pe1 = (end[-1] if end else S[-1])["parent_events"].get("oom_kill", 0)
    insp = dict(kv.split("=") for kv in open(os.path.join(d, "inspect.txt")).read().split())
    o["oom"] = {"container_oom_kill": alive[-1]["events"].get("oom_kill", 0) if alive else None, "parent_oom_kill_delta": pe1 - pe0, "docker_oom_killed": insp.get("oom_killed"), "exit_code": insp.get("exit_code")}
    o["oom_killed"] = o["oom"]["parent_oom_kill_delta"] > 0 or insp.get("oom_killed") == "true"
    n_s = int(m["dur_s"])
    sec = per_second(reqs, t0, n_s, rps)
    o["overload_at_s"] = next((x["s"] for x in sec if x["inflight_at_end"] >= rps or x["timeouts"] > 0), None)
    smaps = os.path.join(d, "smaps.json")
    if os.path.exists(smaps):
        sm = json.load(open(smaps))
        o["smaps"] = {k: sm[k] for k in ("touched_kb", "referenced_kb", "anonymous_in_file_kb", "file_mapped_rss_kb", "exec_rss_kb")}
    info = os.path.join(d, "info.json")
    if os.path.exists(info):
        o["app"] = json.load(open(info))
    if m["mode"] == "p3":
        o["p3"] = p3_series(reqs, t0, S, sec, m)
    json.dump(sec, open(os.path.join(d, "per_second.json"), "w"), indent=1)
    json.dump(o, open(os.path.join(d, "summary.json"), "w"), indent=1)
    return o


def p3_series(reqs, t0, S, sec, m):
    alive = [s for s in S if s["alive"]]
    stop = float(m["container_stopped_unix"]) - t0 if "container_stopped_unix" in m else None
    oom_t = None
    base_k = S[0]["parent_events"].get("oom_kill", 0) if S else 0
    for s in S:
        if s["parent_events"].get("oom_kill", 0) > base_k:
            oom_t = s["t"] - t0
            break
    base_p99 = pct([r["lat"] for r in reqs if r["sched"] < t0 + 10], 0.99)

    def at(t):
        c = [s for s in alive if s["t"] <= t0 + t]
        return c[-1] if c else None

    rows = []
    last = int(reqs[-1]["sched"] - t0) + 1
    for k in range(0, last, 5):
        win = [r["lat"] for r in reqs if t0 + k <= r["sched"] < t0 + k + 5]
        a, b = at(k), at(k + 5)
        done = [x["completed"] for x in sec if k <= x["s"] < k + 5]
        row = {"t_s": k, "n": len(win), "completed_per_s": r2(sum(done) / len(done), 1) if done else None, "p99_ms": r2(pct(win, 0.99), 1), "timeouts": sum(1 for r in reqs if t0 + k <= r["sched"] < t0 + k + 5 and r["timeout"])}
        if b is not None:
            row.update({"anon_mib": r2(b["stat"]["anon"] / MIB, 1), "file_mib": r2(b["stat"]["file"] / MIB, 1), "file_mapped_mib": r2(b["stat"]["file_mapped"] / MIB, 1), "headroom_mib": r2(headroom(b), 2), "oom_kill": b["events"].get("oom_kill", 0)})
        if a is not None and b is not None and b["t"] > a["t"]:
            dt = b["t"] - a["t"]
            row["mem_psi_some_s_per_s"] = r2((b["mem_psi"]["some"] - a["mem_psi"]["some"]) / dt / 1e6, 4)
            row["pgmajfault_per_s"] = r2((b["stat"]["pgmajfault"] - a["stat"]["pgmajfault"]) / dt, 1)
        rows.append(row)
    edge = None
    for a, b in zip(alive, alive[1:]):
        if b["t"] >= t0 and (b["stat"]["pgmajfault"] - a["stat"]["pgmajfault"]) / (b["t"] - a["t"]) > 100:
            edge = b
            break
    an = [(s["t"] - t0, s["stat"]["anon"] / MIB) for s in alive if s["t"] >= t0]
    return {"baseline_p99_ms_first10s": r2(base_p99, 1),
            "first_pgmajfault_over_100_s": r2(edge["t"] - t0, 1) if edge else None,
            "headroom_at_edge_mib": r2(headroom(edge), 2) if edge else None,
            "file_mapped_at_edge_mib": r2(edge["stat"]["file_mapped"] / MIB, 2) if edge else None, "oom_kill_at_s": r2(oom_t, 1), "container_stopped_at_s": r2(stop, 1),
            "anon_growth_mib_per_s": r2(slope([x for x, _ in an], [y for _, y in an]), 4), "rows_5s": rows}


if __name__ == "__main__":
    for d in sys.argv[1:]:
        o = analyze(d)
        print(json.dumps({k: o.get(k) for k in ("label", "latency_ms", "timeouts", "headroom_mib", "file_mapped_mib", "oom_killed")}))
