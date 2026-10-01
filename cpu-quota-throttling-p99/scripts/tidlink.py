import bisect
from collections import defaultdict

def swap16(v):
    v = int(v) & 0xFFFF
    return ((v & 0xFF) << 8) | (v >> 8)


def parse(path):
    ev = defaultdict(list)
    for l in open(path):
        f = l.strip().split(",")
        k = f[0]
        try:
            if k == "R" and len(f) == 6:
                ev[k].append((int(f[1]), int(f[2]), int(f[3]), int(f[4]), int(f[5])))
            elif k == "W" and len(f) == 4:
                ev[k].append((int(f[1]), int(f[2]), int(f[3])))
            elif k == "M" and len(f) == 5:
                ev[k].append((int(f[1]), int(f[2]), int(f[3]), int(f[4])))
            elif k == "C" and len(f) == 9:
                ev[k].append((int(f[1]), int(f[2]), int(f[3]), int(f[4]), int(f[5]), int(f[6]), int(f[7]), int(f[8])))
            elif k == "P" and len(f) == 6:
                ev[k].append((int(f[1]), int(f[2]), int(f[3]), int(f[4]), int(f[5])))
            elif k in ("A", "X") and len(f) == 5:
                ev[k].append((int(f[1]), int(f[2]), int(f[3]), swap16(f[4])))
        except ValueError:
            pass
    for v in ev.values():
        v.sort()
    return ev


def timelines(ev):
    per = defaultdict(list)
    for t, tid, cpu in ev["W"]:
        per[tid].append((t, 0, "W", cpu))
    for t, tid, _, dest in ev["M"]:
        per[tid].append((t, 1, "M", dest))
    unknown = 0
    for a, b, cpu, tid, st in ev["R"]:
        if a == 0:
            unknown += 1
        per[tid].append((a if a else b, 2, "R", (a, b, cpu, st)))
    seg = {}
    for tid, xs in per.items():
        xs.sort()
        out, ws, wc = [], None, None
        for t, _, k, v in xs:
            if k == "W":
                if ws is None:
                    ws, wc = t, v
            elif k == "M":
                if ws is not None:
                    out.append((ws, t, "wait", wc))
                    ws, wc = t, v
            else:
                a, b, cpu, st = v
                if a and ws is not None:
                    out.append((ws, a, "wait", wc))
                if a:
                    out.append((a, b, "run", cpu))
                ws, wc = (b, cpu) if st & 0xFF == 0 else (None, None)
        seg[tid] = sorted(out)
    return seg, unknown


def link_requests(ok, ev):
    acc = defaultdict(list)
    for t, tid, cpu, port in ev["A"]:
        acc[port].append((t, tid))
    snd = defaultdict(list)
    for t, tid, cpu, port in ev["X"]:
        snd[(tid, port)].append(t)
    out = {}
    for r in ok:
        port = int(r.get("src_port") or 0)
        a = [(t, tid) for t, tid in acc.get(port, []) if r["s"] - 1_000_000 <= t <= r["e"]]
        if not a:
            continue
        at, tid = a[0]
        x = [t for t in snd.get((tid, port), []) if at <= t <= r["e"]]
        if not x:
            continue
        out[r["i"]] = {"tid": tid, "accept": at, "send": x[0], "ambiguous": len(a) > 1 or len(x) > 1}
    return out


def overlap(segs, a, b, kinds=("run", "wait"), cpu=None):
    return [(max(s, a), min(e, b), k, c) for s, e, k, c in segs if s < b and e > a and k in kinds and (cpu is None or c == cpu)]


def oncpu(segs, a, b):
    return sum(e - s for s, e, _, _ in overlap(segs, a, b, ("run",)))


def next_run_start(segs, t):
    for s, e, k, _ in segs:
        if k == "run" and e > t:
            return max(s, t)
    return None


def analyze_links(ok, ev, per_cpu, union, crossings):
    if not ev["A"] or not ev["R"]:
        return None
    seg, unknown = timelines(ev)
    links = link_requests(ok, ev)
    cinfo = {(t, cpu): (tid, incg, rr, pool, tm) for t, cpu, ret, tid, incg, rr, pool, tm in ev["C"] if ret == 1}
    timers = {v[4] for v in cinfo.values()}
    periods = [(t, q, rt) for t, _, ptr, q, rt in ev["P"] if ptr in timers]
    pT = [p[0] for p in periods]
    quota = periods[0][1] if periods else None
    ustart = [u[0] for u in union]
    members = defaultdict(list)
    for a, b, c in per_cpu:
        i = bisect.bisect_right(ustart, a) - 1
        members[i].append((a, b, c, cinfo.get((a, c))))

    def period_start(t):
        i = bisect.bisect_right(pT, t) - 1
        return pT[i] if i >= 0 else None

    def cg_oncpu(a, b):
        return sum(oncpu(s, a, b) for s in seg.values())

    def active(a, b):
        return sorted(tid for tid, s in seg.items() if overlap(s, a, b))

    def affect(tid, ui, s, e):
        segs = seg.get(tid, [])
        hits = []
        for a, b, c, ci in members[ui]:
            if ci and ci[0] == tid:
                hits.append((a, "running", c))
            else:
                w = overlap(segs, a, b, ("wait",), c)
                if w:
                    hits.append((w[0][0], "queued", c))
        return sorted(h for h in hits if s <= h[0] < e)

    def per_request(r, L):
        tid, s, e = L["tid"], L["accept"], L["send"]
        segs = seg.get(tid, [])
        th = []
        for ui, u in enumerate(union):
            if u[0] < e and u[1] > s:
                h = affect(tid, ui, s, e)
                stopped = sorted({ci[0] for _, _, _, ci in members[ui] if ci and ci[1]})
                th.append({"ui": ui, "start": u[0], "end": u[1], "hits": h, "stopped_tids": stopped})
        hit = {x["ui"]: x["hits"][0][1] for x in th if x["hits"]}
        pre = [ui for ui, u in enumerate(union) if u[0] < s and u[1] > r["s"] and ui not in hit]
        stages = []
        for ui, u in enumerate(union):
            if u[0] < r["e"] and u[1] > r["s"]:
                stages.append(hit.get(ui) or ("before_accept" if u[0] < s else "after_send" if u[0] >= e else "not_runnable"))
        return th, [x for x in th if x["hits"]], pre, stages

    def segments(L, linked):
        tid, s, e = L["tid"], L["accept"], L["send"]
        segs = seg.get(tid, [])
        bounds, begin = [], s
        for x in linked:
            stop = x["hits"][0][0]
            if stop < begin:
                stop = begin
            bounds.append((begin, stop, x))
            begin = next_run_start(segs, x["end"]) or x["end"]
        bounds.append((begin, e, None))
        segs_out = []
        for b0, b1, x in bounds:
            ps = period_start(b0)
            segs_out.append({
                "begin_rel_period_start_ms": round((b0 - ps) / 1e6, 2) if ps is not None else None,
                "cg_oncpu_since_period_start_ms": round(cg_oncpu(ps, b0) / 1e6, 2) if ps is not None else None,
                "cg_oncpu_period_start_to_end_ms": round(cg_oncpu(ps, b1) / 1e6, 2) if ps is not None else None,
                "req_oncpu_ms": round(oncpu(segs, b0, b1) / 1e6, 2),
                "len_ms": round((b1 - b0) / 1e6, 2),
                "cg_threads_active": len(active(b0, b1)),
                "ends_with": "throttle" if x else "response",
            })
        return segs_out

    thr = [cg_oncpu(period_start(u[0]), u[0]) / 1e6 for u in union if period_start(u[0]) is not None]
    thr.sort()
    at_thr = {"n": len(thr), "min": round(thr[0], 2), "p50": round(thr[len(thr) // 2], 2), "max": round(thr[-1], 2)} if thr else None
    stage_counts = defaultdict(int)
    rows, n_link, n_pre, diff, diff_pre, diff_kinds, amb = {}, 0, 0, 0, 0, defaultdict(int), 0
    for r in ok:
        L = links.get(r["i"])
        if not L:
            continue
        th, linked, pre_u, stages = per_request(r, L)
        for st in stages:
            stage_counts[st] += 1
        old = crossings(r["s"], r["e"])
        pre = len(pre_u)
        new = len(linked)
        n_link += new >= 1
        n_pre += pre >= 1
        if new != old:
            diff += 1
            diff_kinds[f"old{old}_new{new}_pre_accept{pre}"] += 1
        if len(set(pre_u) | {x["ui"] for x in linked}) != old:
            diff_pre += 1
        amb += L["ambiguous"]
        rows[r["i"]] = (L, th, linked, old, pre, stages)

    def fmt(r):
        if r["i"] not in rows:
            return {"lat_ms": round(r["lat"], 1), "linked": False}
        L, th, linked, old, pre, stages = rows[r["i"]]
        return {
            "lat_ms": round(r["lat"], 1), "linked": True, "tid": L["tid"], "link_ambiguous": L["ambiguous"],
            "client_to_accept_ms": round((L["accept"] - r["s"]) / 1e6, 2),
            "server_span_ms": round((L["send"] - L["accept"]) / 1e6, 2),
            "send_to_client_recv_ms": round((r["e"] - L["send"]) / 1e6, 2),
            "throttle_stages": stages,
            "throttles_before_accept": pre,
            "throttles_overlapping_server_span": len(th),
            "throttles_stopping_tid": len(linked), "throttles_time_overlap_old": old,
            "throttles": [{"start_rel_accept_ms": round((x["start"] - L["accept"]) / 1e6, 2), "len_ms": round((x["end"] - x["start"]) / 1e6, 2),
                           "tid_state": x["hits"][0][1] if x["hits"] else "not_runnable",
                           "running_tids_stopped": x["stopped_tids"], "req_tid_was_running": L["tid"] in x["stopped_tids"]} for x in th],
            "segments": segments(L, linked),
        }

    return {
        "quota_ms": round(quota / 1e6, 2) if quota else None,
        "quota_usage_source": "derived: summed on-CPU time (sched_switch) of cgroup threads since the last period timer (sched_cfs_period_timer)",
        "n_requests_linked": len(rows), "n_link_ambiguous": amb, "n": len(ok),
        "n_ge1_stopping_tid": n_link, "n_ge1_before_accept": n_pre,
        "n_differs_from_time_overlap": diff, "differs_by": dict(sorted(diff_kinds.items())),
        "n_differs_counting_before_accept_union": diff_pre,
        "cg_oncpu_period_start_to_throttle_ms": at_thr,
        "cg_threads_seen": len(seg), "switch_out_without_switch_in": unknown,
        "periods_seen": len(periods),
        "throttle_stages_all_requests": dict(sorted(stage_counts.items())),
    }, fmt
