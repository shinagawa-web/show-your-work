"""cpu.stat usage_usec re-aggregated into 100ms windows, vs nr_throttled.

usage: python3 period_usage.py <result dir> [near_ms]

Windows are cut on the 10ms cpu.stat samples (cpustat_10ms.csv). A window
boundary at time t uses the sample nearest to t.

  aligned   boundaries on the CFS period grid (sched_cfs_period_timer, P);
            usage from the samples nearest to each boundary; nr_throttled
            delta from the first samples at or after each boundary (the
            kernel adds to nr_throttled in the period timer at the end of
            the throttled period)
  naive     every 10 samples from the collector's first sample
  offset X  samples nearest to the period grid shifted by +X ms; usage and
            nr_throttled from the same samples

"sample minus boundary" is always measured from the period boundary.

For each window: usage delta (ms), nr_throttled delta, and whether a
throttle_cfs_rq (bpftrace) returning 1 happened inside the window.
Only windows fully inside [first request send, last response recv] count.
"""
import bisect, csv, sys
from collections import Counter
import tidlink
from period_arrivals import grid, timer_fires, PERIOD


def compute(d, near=45.0, ev=None):
    ev = ev or tidlink.parse(f"{d}/throttle_raw.txt")
    pts = timer_fires(ev)
    if not pts:
        return None
    thr_t = sorted(c[0] for c in ev["C"] if c[2] == 1)
    reqs = [r for r in csv.DictReader(open(f"{d}/requests.csv")) if not r["err"]]
    lo = min(int(r["send_mono_ns"]) for r in reqs)
    hi = max(int(r["send_mono_ns"]) + int(r["latency_ns"]) for r in reqs)
    s = list(csv.DictReader(open(f"{d}/cpustat_10ms.csv")))
    st = [int(r["t_mono_ns"]) for r in s]
    use = [int(r["usage_usec"]) for r in s]
    nth = [int(r["nr_throttled"]) for r in s]
    npd = [int(r["nr_periods"]) for r in s]
    gaps = sorted((b - a) / 1e6 for a, b in zip(st, st[1:]))
    b = grid(pts, lo, hi)

    def nearest(t):
        i = bisect.bisect_left(st, t)
        if i > 0 and (i == len(st) or t - st[i - 1] <= st[i] - t):
            i -= 1
        return i

    def windows(bounds, split=False):
        out = []
        for x, y in zip(bounds, bounds[1:]):
            if x < lo or y > hi:
                continue
            i, j = nearest(x), nearest(y)
            ti, tj = (bisect.bisect_left(st, x), bisect.bisect_left(st, y)) if split else (i, j)
            if j >= len(st) or tj >= len(st) or i >= j:
                continue
            k0 = bisect.bisect_left(thr_t, x)
            k1 = bisect.bisect_left(thr_t, y)
            out.append({"x": x, "lag_ms": (st[i] - x) / 1e6, "thr_lag_ms": (st[ti] - x) / 1e6,
                        "usage_ms": (use[j] - use[i]) / 1e3, "dthr": nth[tj] - nth[ti], "bpf": k1 > k0})
        return out

    def med(xs):
        xs = sorted(xs)
        return round(xs[len(xs) // 2], 2)

    def stats(w, bins=False):
        hi_u = [r for r in w if r["usage_ms"] >= near]
        dt = [r for r in w if r["dthr"] > 0]
        bp = [r for r in w if r["bpf"]]
        both = [r for r in hi_u if r["dthr"] > 0]
        o = {"windows": len(w), "usage_ge_near": len(hi_u), "dthr_gt0": len(dt), "both": len(both),
             "p_dthr_given_usage_ge_near": round(len(both) / len(hi_u), 3) if hi_u else None,
             "p_usage_ge_near_given_dthr": round(len(both) / len(dt), 3) if dt else None,
             "bpf_windows": len(bp), "p_usage_ge_near_given_bpf": round(sum(r["usage_ms"] >= near for r in bp) / len(bp), 3) if bp else None,
             "max_usage_ms": round(max(r["usage_ms"] for r in w), 2),
             "usage_sample_minus_boundary_ms_median": med(r["lag_ms"] for r in w),
             "nr_throttled_sample_minus_boundary_ms_median": med(r["thr_lag_ms"] for r in w)}
        if bins:
            c, t, p = Counter(), Counter(), Counter()
            for r in w:
                k = int(r["usage_ms"] // 5 * 5)
                c[k] += 1
                t[k] += r["dthr"] > 0
                p[k] += r["bpf"]
            o["by_usage_ms"] = [{"bin": f"[{k},{k + 5})", "windows": c[k], "dthr_gt0": t[k], "bpf_throttle": p[k]} for k in sorted(c)]
            cc = Counter((r["dthr"] > 0, r["bpf"]) for r in w)
            o["dthr_vs_bpf"] = {"dthr_and_bpf": cc[(True, True)], "dthr_only": cc[(True, False)], "bpf_only": cc[(False, True)], "neither": cc[(False, False)]}
        return o

    iw = [i for i, t in enumerate(st) if lo <= t <= hi]
    aligned = windows(b, split=True)
    naive_b = [st[i] for i in range(0, len(st), 10)]
    naive = windows(naive_b)
    sweep = []
    for off in range(0, 100, 10):
        w = windows([x + off * 1_000_000 for x in b])
        x = stats(w)
        for k in ("usage_sample_minus_boundary_ms_median", "nr_throttled_sample_minus_boundary_ms_median"):
            x[k] = round(x[k] + off, 2)
        sweep.append({"offset_ms": off, **x})
    out = {
        "near_ms": near,
        "samples": len(s), "sample_interval_ms": {"min": round(gaps[0], 2), "p50": round(gaps[len(gaps) // 2], 2), "max": round(gaps[-1], 2)},
        "load_window": {"nr_throttled_delta": nth[iw[-1]] - nth[iw[0]], "nr_periods_delta": npd[iw[-1]] - npd[iw[0]],
                        "usage_ms": round((use[iw[-1]] - use[iw[0]]) / 1e3, 1)},
        "bpf_throttle_calls": sum(1 for t in thr_t if lo <= t <= hi),
        "bpf_throttled_periods": len({bisect.bisect_right(b, t) - 1 for t in thr_t if lo <= t <= hi}),
        "naive_phase_ms": round(((naive_b[0] - b[0]) % PERIOD) / 1e6, 2),
        "aligned": stats(aligned, True),
        "naive": stats(naive, True),
        "offset_sweep": sweep,
    }
    return out, aligned, naive


def main():
    d = sys.argv[1]
    near = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0
    res = compute(d, near)
    if res is None:
        sys.exit(f"{d}: no throttle records, period timer unknown")
    o, aligned, naive = res
    print(f"# {d}")
    si = o["sample_interval_ms"]
    print(f"samples={o['samples']} interval_ms min={si['min']} median={si['p50']} max={si['max']}")
    lw = o["load_window"]
    print(f"load window: nr_throttled delta={lw['nr_throttled_delta']} nr_periods delta={lw['nr_periods_delta']} usage_ms={lw['usage_ms']}")
    print(f"bpftrace: throttle calls in load window={o['bpf_throttle_calls']} distinct periods={o['bpf_throttled_periods']}")
    print(f"naive grid phase relative to period grid: +{o['naive_phase_ms']} ms")
    keys = ["windows", "usage_ge_near", "dthr_gt0", "both", "p_dthr_given_usage_ge_near", "p_usage_ge_near_given_dthr",
            "bpf_windows", "p_usage_ge_near_given_bpf", "max_usage_ms", "usage_sample_minus_boundary_ms_median", "nr_throttled_sample_minus_boundary_ms_median"]
    for name in ("aligned", "naive"):
        x = o[name]
        print(f"\n{name}\t" + "\t".join(f"{k}={x[k]}" for k in keys))
        print("usage_ms_bin\twindows\tdthr>0\tbpf_throttle")
        for r in x["by_usage_ms"]:
            print(f"{r['bin']}\t{r['windows']}\t{r['dthr_gt0']}\t{r['bpf_throttle']}")
        print("dthr vs bpf: " + " ".join(f"{k}={v}" for k, v in x["dthr_vs_bpf"].items()))
    print("\n## offset sweep (period grid shifted by +X ms, usage and nr_throttled from the same samples)")
    for x in o["offset_sweep"]:
        print(f"offset {x['offset_ms']}\t" + "\t".join(f"{k}={x[k]}" for k in keys))
    with open(f"{d}/period_usage.csv", "w") as f:
        wr = csv.writer(f)
        wr.writerow(["window", "start_mono_ns", "sample_lag_ms", "usage_ms", "nr_throttled_delta", "bpf_throttle"])
        for name, w in (("aligned", aligned), ("naive", naive)):
            for r in w:
                wr.writerow([name, r["x"], f"{r['lag_ms']:.3f}", f"{r['usage_ms']:.3f}", r["dthr"], int(r["bpf"])])


if __name__ == "__main__":
    main()
