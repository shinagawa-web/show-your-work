"""Per CFS period: requests that arrived vs. whether the cgroup was throttled.

usage: python3 period_arrivals.py <result dir> [carry_threshold_ms]

Periods are cut on the cgroup's own sched_cfs_period_timer (P) timestamps.
When the timer is inactive (idle cgroup) the gap is filled with a 100ms grid
that keeps the timer's phase. All timestamps are CLOCK_MONOTONIC.

Per period k = [b_k, b_k+1):
  arrivals   requests whose load generator send_mono_ns falls in the period
  arr_accept requests whose server accept (inet_csk_accept) falls in the period
  throttled  at least one throttle_cfs_rq returning 1 in the period
  carry_ms   on-CPU time in the period of requests sent before b_k
             (request thread run segments between accept and response send)
  cg_ms      on-CPU time in the period of all cgroup threads (sched_switch)
  fit_ms     sum over arrivals of min(20ms, time left in the period at send)
"""
import bisect, csv, sys
from collections import Counter, defaultdict
import tidlink

PERIOD = 100_000_000


def load(d, ev=None):
    ev = ev or tidlink.parse(f"{d}/throttle_raw.txt")
    reqs = [r for r in csv.DictReader(open(f"{d}/requests.csv")) if not r["err"]]
    for r in reqs:
        r["i"] = int(r["i"])
        r["s"] = int(r["send_mono_ns"])
        r["e"] = r["s"] + int(r["latency_ns"])
    return ev, timer_fires(ev), reqs


def timer_fires(ev):
    """Firings of the period timer of the traced cgroup, identified from throttle records."""
    timers = {c[7] for c in ev["C"] if c[2] == 1}
    if len(timers) != 1:
        return None
    return sorted(p[0] for p in ev["P"] if p[2] in timers)


def grid(pts, lo, hi):
    """Period boundaries covering [lo, hi): real P timestamps, gaps filled at +100ms."""
    b = []
    t = pts[0]
    while t > lo:
        t -= PERIOD
    i = 0
    while t < hi:
        b.append(t)
        nxt = t + PERIOD
        while i < len(pts) and pts[i] <= t + PERIOD // 2:
            i += 1
        if i < len(pts) and abs(pts[i] - nxt) < PERIOD // 2:
            nxt = pts[i]
        t = nxt
    b.append(t)
    return b


def compute(d, thr_ms=0.5, ev=None):
    ev, pts, reqs = load(d, ev)
    if not pts:
        return None
    lo = min(r["s"] for r in reqs)
    hi = max(r["e"] for r in reqs)
    b = grid(pts, lo, hi)
    n = len(b) - 1
    real = set(pts)

    def idx(t):
        return bisect.bisect_right(b, t) - 1

    arr = Counter(idx(r["s"]) for r in reqs)
    offs = defaultdict(list)
    for r in reqs:
        k = idx(r["s"])
        if 0 <= k < n:
            offs[k].append((r["s"] - b[k]) / 1e6)
    thr = Counter(idx(c[0]) for c in ev["C"] if c[2] == 1)

    seg, _ = tidlink.timelines(ev)
    links = tidlink.link_requests(reqs, ev)
    acc = Counter(idx(L["accept"]) for L in links.values())
    carry = defaultdict(int)
    for r in reqs:
        L = links.get(r["i"])
        if not L:
            continue
        s = seg.get(L["tid"], [])
        k0 = idx(r["s"])
        for k in range(max(k0 + 1, idx(L["accept"])), idx(L["send"]) + 1):
            if 0 <= k < n:
                carry[k] += tidlink.oncpu(s, max(L["accept"], b[k]), min(L["send"], b[k + 1]))
    cg = defaultdict(int)
    for tid, s in seg.items():
        for a, e, kind, _ in s:
            if kind != "run":
                continue
            for k in range(max(idx(a), 0), min(idx(e), n - 1) + 1):
                cg[k] += min(e, b[k + 1]) - max(a, b[k])

    rows = []
    for k in range(n):
        rows.append({"k": k, "start": b[k], "real_timer": b[k] in real, "arrivals": arr[k], "arr_accept": acc[k],
                     "throttled": thr[k] > 0, "n_throttle_calls": thr[k],
                     "carry_ms": carry[k] / 1e6, "cg_ms": cg[k] / 1e6,
                     "fit_ms": sum(min(20.0, 100.0 - o) for o in offs[k]),
                     "offsets_ms": " ".join(f"{o:.1f}" for o in sorted(offs[k]))})
    has = lambda r: r["carry_ms"] > thr_ms

    def table(sel, key="arrivals"):
        c, t = Counter(), Counter()
        for r in sel:
            x = r[key]
            c[x] += 1
            t[x] += r["throttled"]
        return [{"bin": x, "periods": c[x], "throttled": t[x], "fraction": round(t[x] / c[x], 3)} for x in sorted(c)]

    def binned(fn, width):
        c, t = Counter(), Counter()
        for r in rows:
            x = int(fn(r) // width * width)
            c[x] += 1
            t[x] += r["throttled"]
        return [{"bin": f"[{x},{x + width})", "periods": c[x], "throttled": t[x], "fraction": round(t[x] / c[x], 3)} for x in sorted(c)]

    tp = [r for r in rows if r["throttled"]]
    in_thr = defaultdict(lambda: {"all": 0, "no_carry": 0, "carry": 0})
    for r in tp:
        in_thr[r["arrivals"]]["all"] += 1
        in_thr[r["arrivals"]]["carry" if has(r) else "no_carry"] += 1
    ge3 = [r for r in rows if r["arrivals"] >= 3 and not has(r) and not r["throttled"]]
    out = {
        "carry_threshold_ms": thr_ms,
        "periods": n, "periods_real_timer": sum(1 for x in b[:-1] if x in real), "requests": len(reqs), "requests_linked": len(links),
        "throttle_calls": sum(thr.values()), "throttled_periods": len(tp),
        "throttle_calls_outside_periods": sum(v for k, v in thr.items() if not 0 <= k < n),
        "by_arrivals_all": table(rows),
        "by_arrivals_no_carry": table([r for r in rows if not has(r)]),
        "by_arrivals_carry": table([r for r in rows if has(r)]),
        "by_accepts_no_carry": table([r for r in rows if not has(r)], "arr_accept"),
        "by_accepts_carry": table([r for r in rows if has(r)], "arr_accept"),
        "arrivals_in_throttled_periods": [{"arrivals": a, **in_thr[a]} for a in sorted(in_thr)],
        "by_demand_arrivals_x20_plus_carry_ms": binned(lambda r: r["arrivals"] * 20 + r["carry_ms"], 10),
        "by_demand_in_period_ms": binned(lambda r: r["fit_ms"] + r["carry_ms"], 10),
        "by_cgroup_oncpu_ms": binned(lambda r: r["cg_ms"], 5),
        "ge3_arrivals_no_carry_not_throttled": [{"k": r["k"], "arrivals": r["arrivals"], "offsets_ms": r["offsets_ms"], "cg_ms": round(r["cg_ms"], 2),
                                                 "next_period_throttled": rows[r["k"] + 1]["throttled"] if r["k"] + 1 < n else None} for r in ge3],
        "ge3_arrivals_no_carry_not_throttled_min_last_arrival_ms": min((max(float(x) for x in r["offsets_ms"].split()) for r in ge3), default=None),
    }
    return rows, out, tp


def main():
    d = sys.argv[1]
    thr_ms = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
    res = compute(d, thr_ms)
    if res is None:
        sys.exit(f"{d}: no throttle records, period timer unknown")
    rows, o, tp = res
    with open(f"{d}/period_arrivals.csv", "w") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({**r, "carry_ms": f"{r['carry_ms']:.3f}", "cg_ms": f"{r['cg_ms']:.3f}", "fit_ms": f"{r['fit_ms']:.3f}"})
    print(f"# {d}")
    print(f"periods={o['periods']} (start at a real timer firing: {o['periods_real_timer']}, grid-filled: {o['periods'] - o['periods_real_timer']}) "
          f"requests={o['requests']} linked={o['requests_linked']} throttle_calls={o['throttle_calls']} "
          f"throttled_periods={o['throttled_periods']} carry_threshold_ms={thr_ms}")
    for key, title in (("by_arrivals_all", "all periods (by arrivals)"),
                       ("by_arrivals_no_carry", f"no carry-over (carry_ms <= {thr_ms}) (by arrivals)"),
                       ("by_arrivals_carry", f"carry-over (carry_ms > {thr_ms}) (by arrivals)"),
                       ("by_accepts_no_carry", f"no carry-over (carry_ms <= {thr_ms}) (by arr_accept)"),
                       ("by_accepts_carry", f"carry-over (carry_ms > {thr_ms}) (by arr_accept)"),
                       ("by_demand_arrivals_x20_plus_carry_ms", "demand = arrivals*20ms + carry_ms, vs throttled"),
                       ("by_demand_in_period_ms", "in-period demand = sum(min(20ms, time left in period at arrival)) + carry_ms, vs throttled"),
                       ("by_cgroup_oncpu_ms", "cgroup on-CPU in period (sched_switch), vs throttled")):
        print(f"\n## {title}")
        print("bin\tperiods\tthrottled\tfraction")
        for r in o[key]:
            print(f"{r['bin']}\t{r['periods']}\t{r['throttled']}\t{r['fraction']:.3f}")
    print("\n## arrivals in throttled periods")
    print("arrivals\tall\tno_carry\tcarry")
    for r in o["arrivals_in_throttled_periods"]:
        print(f"{r['arrivals']}\t{r['all']}\t{r['no_carry']}\t{r['carry']}")
    print("\n## periods with arrivals >= 3 and no carry-over that did not throttle")
    for r in o["ge3_arrivals_no_carry_not_throttled"]:
        print(f"k={r['k']}\tarrivals={r['arrivals']}\toffsets_ms={r['offsets_ms']}\tcg_ms={r['cg_ms']:.2f}\tnext_period_throttled={r['next_period_throttled']}")
    print("\n## throttled periods: arrivals, carry_ms, cg_ms")
    for r in tp:
        print(f"k={r['k']}\tarrivals={r['arrivals']}\toffsets_ms={r['offsets_ms']}\tarr_accept={r['arr_accept']}\tcarry_ms={r['carry_ms']:.2f}\tcg_ms={r['cg_ms']:.2f}\tcalls={r['n_throttle_calls']}")


if __name__ == "__main__":
    main()
