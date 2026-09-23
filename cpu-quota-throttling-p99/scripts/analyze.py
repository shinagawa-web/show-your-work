import bisect, csv, json, os, statistics, sys

SKIP = "skipped (bpftrace unavailable)"


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else None


def rows(path):
    return list(csv.DictReader(open(path)))


def merge(iv):
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def wall_segments(s10):
    segs = []
    for r in s10:
        off = int(r["t_unix_ns"]) - int(r["t_mono_ns"])
        if not segs or abs(off - segs[-1][1]) > 1_000_000:
            segs.append((int(r["t_mono_ns"]), off))
    return segs


def wall_to_mono(w, segs):
    for k in range(len(segs) - 1, -1, -1):
        m = w - segs[k][1]
        if m >= segs[k][0] and (k == len(segs) - 1 or m < segs[k + 1][0]):
            return m
    return w - segs[0][1]


def send_mono_from_schedule(r, t0_wall, segs):
    base = segs[0][1]
    t0_mono = wall_to_mono(t0_wall, segs)
    late = int(r["send_unix_ns"]) - t0_wall - int(r["sched_ns"])
    fixes = [late - (off - base) for _, off in segs]
    ok = [f for f in fixes if -5_000_000 <= f <= 50_000_000]
    return t0_mono + int(r["sched_ns"]) + (min(ok, key=abs) if ok else 0)


def recvq_mono(rq_rows, segs):
    if rq_rows and rq_rows[0].get("t_mono_ns"):
        return [(int(r["t_mono_ns"]), int(r["recv_q"])) for r in rq_rows]
    out, k, prev = [], 0, None
    for r in rq_rows:
        w = int(r["t_unix_ns"])
        if prev is not None and w - prev < -1_000_000 and k + 1 < len(segs):
            k += 1
        prev = w
        out.append((w - segs[k][1], int(r["recv_q"])))
    return out


def analyze(d):
    meta = {}
    for l in open(f"{d}/meta.txt"):
        for kv in l.split() if l.startswith(("label=", "sent=")) else [l.strip()]:
            if "=" in kv:
                k, v = kv.split("=", 1)
                meta[k] = v
    cpus, dur = float(meta["cpus"]), float(meta["dur"])
    bpf = meta.get("bpftrace") == "1" and os.path.exists(f"{d}/throttle_raw.txt")

    s10, s1 = rows(f"{d}/cpustat_10ms.csv"), rows(f"{d}/cpustat_1s.csv")
    for r in s10 + s1:
        r["t"] = int(r["t_mono_ns"])
    segs = wall_segments(s10)

    reqs = rows(f"{d}/requests.csv")
    ok = [r for r in reqs if not r["err"]]
    for r in ok:
        r["s"] = int(r["send_mono_ns"]) if int(r.get("send_mono_ns") or 0) > 0 else send_mono_from_schedule(r, int(meta["t0"]), segs)
        r["e"] = r["s"] + int(r["latency_ns"])
        r["lat"] = (r["e"] - r["s"]) / 1e6
    t0 = min(r["s"] for r in ok)
    t1 = t0 + int(dur * 1e9)

    ev = []
    for l in open(f"{d}/throttle_raw.txt") if bpf else []:
        f = l.strip().split(",")
        if f[0] == "T" and len(f) == 4 and f[3] == "1":
            ev.append(("T", int(f[1]), int(f[2])))
        elif f[0] == "U" and len(f) == 3:
            ev.append(("U", int(f[1]), int(f[2])))
    ev.sort(key=lambda e: e[1])
    open_t, per_cpu = {}, []
    for k, t, c in ev:
        if k == "T":
            open_t[c] = t
        elif c in open_t:
            per_cpu.append((open_t.pop(c), t, c))
    union = merge([[a, b] for a, b, _ in per_cpu])
    union_w = [u for u in union if t0 <= u[0] < t1]
    per_cpu_w = [p for p in per_cpu if t0 <= p[0] < t1]

    def util_between(x, y):
        return (int(y["usage_usec"]) - int(x["usage_usec"])) / 1e6 / ((y["t"] - x["t"]) / 1e9)

    def nearest(series, t):
        return min(series, key=lambda r: abs(r["t"] - t))

    a, b = nearest(s1, t0), nearest(s1, t1)
    ia, ib = s1.index(a), s1.index(b)
    dd = {k: int(b[k]) - int(a[k]) for k in ["usage_usec", "nr_periods", "nr_throttled", "throttled_usec"]}
    win = (b["t"] - a["t"]) / 1e9
    util1 = [util_between(x, y) / cpus * 100 for x, y in zip(s1[ia:ib], s1[ia + 1:ib + 1])]

    T10 = [r["t"] for r in s10]
    NT10 = [int(r["nr_throttled"]) for r in s10]
    c10 = []
    for x, y in zip(s10, s10[1:]):
        if t0 <= x["t"] < t1:
            c10.append((x["t"], y["t"], util_between(x, y), x, y))

    def in_union(a, b):
        i = bisect.bisect_right([u[0] for u in union], b)
        return any(u[1] > a for u in union[max(0, i - 1):i])

    def throttled_by_counter(x, y):
        return int(y["throttled_usec"]) > int(x["throttled_usec"]) or int(y["nr_throttled"]) > int(x["nr_throttled"])

    thr_cls = {True: [0, 0, 0], False: [0, 0, 0]}
    for a_, b_, _, x, y in c10:
        k = in_union(a_, b_) if bpf else throttled_by_counter(x, y)
        thr_cls[k][0] += (b_ - a_) // 1000
        thr_cls[k][1] += int(y["cg_some_total"]) - int(x["cg_some_total"])
        thr_cls[k][2] += int(y["cg_full_total"]) - int(x["cg_full_total"])

    rq = recvq_mono(rows(f"{d}/recvq_10ms.csv"), segs)
    rqT = [x for x, _ in rq]
    rq_w = [v for t, v in rq if t0 <= t < t1]
    rq_gaps = [(y - x) / 1e6 for x, y in zip(rqT, rqT[1:])]

    def rq_at(t):
        i = bisect.bisect_right(rqT, t) - 1
        return rq[i][1] if i >= 0 else None

    def conc_at(t):
        for a_, b_, u, _, _ in c10:
            if a_ <= t < b_:
                return round(u, 2)

    def crossings(s, e):
        return sum(1 for u in union if u[0] < e and u[1] > s)

    def nr_thr(s, e):
        i = max(0, bisect.bisect_right(T10, s) - 1)
        j = min(len(T10) - 1, bisect.bisect_left(T10, e))
        return NT10[j] - NT10[i]

    lat = [r["lat"] for r in ok]
    p99 = pct(lat, 99)
    slow = sorted([r for r in ok if r["lat"] >= p99], key=lambda r: -r["lat"])
    hist = {}
    for v in lat:
        hist[int(v // 10) * 10] = hist.get(int(v // 10) * 10, 0) + 1
    ulen = [(u[1] - u[0]) / 1e6 for u in union_w]
    clen = [(p[1] - p[0]) / 1e6 for p in per_cpu_w]
    first10 = [v for t, v in rq if t0 <= t < t0 + 10e9]
    last10 = [v for t, v in rq if t1 - 10e9 <= t < t1]

    return {
        "cond": {k: meta.get(k) for k in ["cpus", "W", "rps", "mode", "dur", "seed", "burst", "cpu_ms"]},
        "host": meta.get("host"), "cpu_model": meta.get("cpu_model"), "nproc": meta.get("nproc"),
        "cgroup": meta["cgroup"], "cpu.max": meta["cpu.max"],
        "bpftrace": bpf,
        "n_requests": len(reqs), "n_errors": len(reqs) - len(ok),
        "latency_ms": {"p50": round(pct(lat, 50), 2), "p99": round(p99, 2), "max": round(max(lat), 2)},
        "util_window_pct": round(dd["usage_usec"] / 1e6 / win / cpus * 100, 1), "window_s": round(win, 3),
        "util_1s_pct": {"min": round(min(util1), 1), "max": round(max(util1), 1), "n_ge90": sum(u >= 90 for u in util1), "n": len(util1)},
        "delta_window": dd,
        "throttle_rate": round(dd["nr_throttled"] / dd["nr_periods"], 4) if dd["nr_periods"] else None,
        "throttled_usec_per_nr_throttled": round(dd["throttled_usec"] / dd["nr_throttled"], 1) if dd["nr_throttled"] else None,
        "kprobe": SKIP if not bpf else {
            "throttle_events_in_window_per_cpu": len(per_cpu_w),
            "throttle_windows_in_window_union": len(union_w),
            "union_len_ms": {"p50": round(pct(ulen, 50), 2), "max": round(max(ulen), 2), "min": round(min(ulen), 2)} if ulen else None,
            "per_cpu_len_ms": {"p50": round(pct(clen, 50), 2), "max": round(max(clen), 2), "sum": round(sum(clen), 1)} if clen else None,
            "unmatched_T": len(open_t),
        },
        "cpus_in_use_10ms": {"mean": round(statistics.mean(u for _, _, u, _, _ in c10), 3), "max": round(max(u for _, _, u, _, _ in c10), 2),
                              "p99": round(pct([u for _, _, u, _, _ in c10], 99), 2)},
        "pressure": {
            "cg_some_avg10_max": max(float(r["cg_some_avg10"]) for r in s1[ia:ib + 1]),
            "sys_some_avg10_max": max(float(r["sys_some_avg10"]) for r in s1[ia:ib + 1]),
            "throttle_interval_source": "kprobe" if bpf else "10ms cpu.stat nr_throttled / throttled_usec increase",
            "10ms_overlapping_throttle": {"elapsed_us": thr_cls[True][0], "cg_some_us": thr_cls[True][1], "cg_full_us": thr_cls[True][2]},
            "10ms_not_overlapping": {"elapsed_us": thr_cls[False][0], "cg_some_us": thr_cls[False][1], "cg_full_us": thr_cls[False][2]},
        },
        "recvq": {"max": max(rq_w) if rq_w else None, "mean": round(statistics.mean(rq_w), 3) if rq_w else None,
                  "mean_first10s": round(statistics.mean(first10), 3) if first10 else None, "mean_last10s": round(statistics.mean(last10), 3) if last10 else None,
                  "sample_gap_ms": {"p50": round(pct(rq_gaps, 50), 2), "max": round(max(rq_gaps), 2)} if rq_gaps else None},
        "requests_crossing_throttle": {"n_ge1": sum(crossings(r["s"], r["e"]) >= 1 for r in ok) if bpf else SKIP,
                                       "n_ge1_nr_throttled_10ms": sum(nr_thr(r["s"], r["e"]) >= 1 for r in ok), "n": len(ok)},
        "slow_ge_p99": [{"lat_ms": round(r["lat"], 1), "kprobe_crossings": crossings(r["s"], r["e"]) if bpf else SKIP, "nr_throttled_delta_10ms": nr_thr(r["s"], r["e"]),
                         "recvq_at_send": rq_at(r["s"]), "cpus_in_use_at_send": conc_at(r["s"])} for r in slow],
        "hist_latency_10ms": dict(sorted(hist.items())),
        "util_1s_series_pct": [round(u, 1) for u in util1],
    }


if __name__ == "__main__":
    for d in sys.argv[1:]:
        o = analyze(d)
        json.dump(o, open(f"{d}/summary.json", "w"), indent=1)
