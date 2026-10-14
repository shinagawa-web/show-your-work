import re

SYNC = 15
JITTER = 2
WINDOW_DEFAULT = 300
WINDOW_C2 = 120
STATUS_LAG = 3
STABLE = 3


def sec(t):
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


class Clock:
    def __init__(self, ref):
        self.ref = ref

    def __call__(self, t):
        v = sec(t)
        if v - self.ref < -43200:
            v += 86400
        elif v - self.ref > 43200:
            v -= 86400
        return v


def snapshot(body):
    lines = body.strip("\n").split("\n")
    replicas = None
    entries = []
    for ln in lines:
        m = re.match(r"^spec\.replicas=(\S+)$", ln)
        if m:
            replicas = m.group(1)
            continue
        parts = ln.split("\t")
        if len(parts) == 5:
            entries.append(dict(zip(("manager", "operation", "subresource", "time", "replicas"), parts)))
    owners = [e for e in entries if e["replicas"] == "owns spec.replicas"]
    return {"replicas": replicas, "entries": entries, "owners": owners}


def owner_str(s):
    return ", ".join(f"{e['manager']}/{e['operation']}/{e['subresource']}/{e['time']}" for e in s["owners"]) or "-"


def parse_cond(text):
    sections = []
    for m in re.finditer(r"^## (\S+(?: operation)?) (\d\d:\d\d:\d\d\.\d+)(.*)\n((?:(?!## ).*\n?)*)", text, re.M):
        sections.append({"kind": m.group(1), "t": m.group(2), "extra": m.group(3).strip(), "body": m.group(4)})
    return sections


def cmds(body):
    return [(m.group(1), m.group(2), int(m.group(3))) for m in re.finditer(r"^\$ (.*)\n((?:(?!\$ |\[exit \d+\]$).*\n)*)\[exit (\d+)\]$", body, re.M)]


class Data:
    def __init__(self, results, names):
        self.names = names
        self.missing = {}
        self.cond = {}
        for n in names:
            f = results / f"{n}.txt"
            self.cond[n] = parse_cond(f.read_text()) if f.exists() else []
        op_times = [s["t"] for n in names for s in self.cond[n] if s["kind"] == "operation"]
        self.clock = Clock(sec(op_times[0]) if op_times else 0)
        self.syncs = {n: [] for n in names}
        for ln in self.read(results, "sync.log"):
            p = ln.split(" ", 3)
            if len(p) >= 3 and p[1] in self.syncs:
                self.syncs[p[1]].append((self.clock(p[0]), p[0], p[2], p[3] if len(p) > 3 else ""))
        self.hpa = {n: [] for n in names}
        for ln in self.read(results, "hpa.log"):
            p = ln.split(" ")
            if len(p) > 2 and p[1] in self.hpa:
                kv = dict(x.split("=", 1) for x in p[2:] if "=" in x)
                self.hpa[p[1]].append((self.clock(p[0]), kv))
        self.pods = []
        for ln in self.read(results, "pods.log"):
            p = ln.split(" ")
            if len(p) > 2:
                self.pods.append((self.clock(p[0]), p[1], dict(x.split("=", 1) for x in p[2:] if "=" in x)))
        self.metrics = {}
        for ln in self.read(results, "metrics.log"):
            p = ln.split(" ")
            if len(p) > 1:
                self.metrics[p[1]] = (self.clock(p[0]), p[0], ln.split(" ", 2)[2] if len(p) > 2 else "")
        self.events = {}
        cur = None
        for ln in self.read(results, "events.txt"):
            m = re.match(r"^## (\S+)$", ln)
            if m:
                cur = m.group(1)
                self.events[cur] = []
            elif cur:
                self.events[cur].append(ln)

    def read(self, results, name):
        f = results / name
        return f.read_text().splitlines() if f.exists() else []

    def sec_of(self, name, kind):
        return next((s for s in self.cond[name] if s["kind"] == kind), None)

    def op(self, name):
        s = self.sec_of(name, "operation")
        return self.clock(s["t"]) if s else None

    def sync_points(self, name, after):
        pts = []
        for t, raw, kind, rest in self.syncs[name]:
            if t <= after:
                continue
            if kind == "proposing":
                pts.append({"t": t, "raw": raw, "proposing": rest, "result": None})
            elif kind in ("decided", "rescaled", "error"):
                if pts and pts[-1]["result"] is None and t - pts[-1]["t"] < 1:
                    pts[-1]["result"] = (kind, rest)
                else:
                    pts.append({"t": t, "raw": raw, "proposing": None, "result": (kind, rest)})
        for p in pts:
            p["able"] = self.able_at(name, p["t"] + STATUS_LAG)
        return pts

    def able_at(self, name, t):
        v = "-"
        for ht, kv in self.hpa[name]:
            if ht <= t:
                v = kv.get("AbleToScale", "-")
        return v

    def new_pods(self, name, after):
        rx = re.compile(rf"^{re.escape(name)}-[a-z0-9]+-[a-z0-9]+$")
        before = {p for t, p, _ in self.pods if rx.match(p) and t <= after}
        return sorted({p for t, p, _ in self.pods if rx.match(p) and t > after} - before)

    def phase_at(self, pod, t):
        v = "not seen"
        for pt, p, kv in self.pods:
            if p == pod and pt <= t:
                v = kv.get("phase", "-")
        return v

    def changes(self, name):
        return [(self.clock(s["t"]), s["t"], s["extra"], snapshot(s["body"])) for s in self.cond[name] if s["kind"] == "change"]


def basic_missing(d, name, ops_ok_codes=(0,)):
    m = []
    for kind in ("before", "operation", "after operation", "end"):
        if d.sec_of(name, kind) is None:
            m.append(f"{name}: no '{kind}' section")
    s = d.sec_of(name, "operation")
    if s:
        cs = cmds(s["body"])
        if not cs:
            m.append(f"{name}: operation not run")
        for c, _, code in cs:
            if code not in ops_ok_codes:
                m.append(f"{name}: '{c}' exited {code}")
    op = d.op(name)
    if op is not None and not d.sync_points(name, op):
        m.append(f"{name}: no HPA sync after the operation in sync.log")
    if op is not None:
        before = [(raw, kind) for t, raw, kind, _ in d.syncs[name] if t <= op and kind in ("decided", "rescaled", "error")][-STABLE:]
        if len(before) < STABLE or any(k != "decided" for _, k in before):
            m.append(f"{name}: the last {STABLE} HPA syncs before the operation did not all decide from metrics: {before}")
        errors = [raw for t, raw, kind, _ in d.syncs[name] if t > op and kind == "error"]
        if errors:
            m.append(f"{name}: HPA syncs after the operation failed: {errors}")
    return m


def fmt(t, d):
    return t if isinstance(t, str) else "-"


def sync_str(p):
    prop = p["proposing"].split()[0] if p["proposing"] else None
    res = " ".join(p["result"]) if p["result"] else None
    return f"{p['raw']} proposing {prop} {res} {p['able']}"


def first_rescale(pts):
    return next((p for p in pts if p["result"] and p["result"][0] == "rescaled"), None)


def held_then_reverted(d, name, window):
    missing = basic_missing(d, name)
    op = d.op(name)
    if op is None:
        return missing, False, "no operation", {}
    pts = d.sync_points(name, op)
    if not pts:
        return missing, False, "no sync", {}
    newp = d.new_pods(name, op)
    if not newp:
        missing.append(f"{name}: no new Pod after the operation")
    unseen = [p for p in newp if p not in d.metrics]
    if unseen:
        missing.append(f"{name}: new Pods never in the metrics API: {unseen}")
    seen = [d.metrics[p][0] for p in newp if p in d.metrics]
    mt = max(seen) if newp and not unseen else None
    rev = first_rescale(pts)
    info = {"op": op, "first": pts[0], "rev": rev, "new": newp}
    no_metrics = [p for p in pts if mt is not None and p["t"] < mt]
    with_metrics = [p for p in pts if mt is not None and p["t"] > mt and (rev is None or p["t"] < rev["t"])]
    is_hold = lambda p: p["proposing"] is not None and p["proposing"].startswith("desired=4 ") and p["result"] == ("decided", "desired=4")
    held = bool(no_metrics) and all(is_hold(p) for p in no_metrics)
    stabilized = bool(with_metrics) and all(p["able"] == "True/ScaleDownStabilized" for p in with_metrics)
    holds_all = [p for p in pts if p["proposing"] is not None and p["proposing"].startswith("desired=4 ")]
    last_hold = holds_all[-1] if holds_all else None
    reverted = bool(rev) and "desired=2" in rev["result"][1]
    delta = rev["t"] - last_hold["t"] if rev and last_hold else None
    timed = delta is not None and window - JITTER <= delta <= window + SYNC + JITTER
    after = d.sec_of(name, "after operation")
    chs = d.changes(name)
    back = next((c for c in chs if c[2].endswith("-> 2")), None)
    s_after = snapshot(after["body"]) if after else None
    mraw = max((d.metrics[p][1] for p in newp if p in d.metrics), default="-")
    detail = (
        f"op {d.sec_of(name, 'operation')['t']}; new Pods all in metrics API {mraw}; "
        f"syncs before that [{', '.join(map(sync_str, no_metrics))}]; "
        f"AbleToScale after that until revert {sorted({p['able'] for p in with_metrics})}; "
        f"last proposing 4 {last_hold['raw'] if last_hold else '-'}; revert sync {rev['raw'] if rev else '-'} {rev['result'][1] if rev else ''}; "
        f"last proposing 4 -> revert {f'{delta:.1f}s' if delta is not None else '-'}; spec.replicas back to 2 seen {back[1] if back else '-'}; "
        f"held while no metrics {held}, ScaleDownStabilized after metrics {stabilized}, reverted to 2 {reverted}, last proposing 4 -> revert in range {timed}"
    )
    info.update({"held": held, "stabilized": stabilized, "reverted": reverted, "timed": timed, "delta": delta, "after": s_after, "back": back})
    return missing, held and stabilized and reverted and timed, detail, info


def p1(d):
    missing, holds, details = [], True, []
    for n in [x for x in d.names if x.startswith("c1-")]:
        m, h, det, info = held_then_reverted(d, n, WINDOW_DEFAULT)
        missing += m
        newp = info.get("new", [])
        s_after = info.get("after")
        back = info.get("back")
        before_ok = bool(s_after) and [(e["manager"], e["operation"], e["subresource"]) for e in s_after["owners"]] == [("kubectl", "Update", "scale")]
        after_ok = bool(back) and [(e["manager"], e["operation"], e["subresource"]) for e in back[3]["owners"]] == [("kube-controller-manager", "Update", "scale")] and not any(e["manager"] == "kubectl" for e in back[3]["entries"])
        ev = any("SuccessfulRescale" in e and "New size: 2;" in e for e in d.events.get(n, []))
        first = info.get("first")
        metric_times = ", ".join(f"{p} {d.metrics[p][1]}" for p in newp if p in d.metrics)
        phases = ", ".join(f"{p} {d.phase_at(p, first['t'])}" for p in newp) if first else "-"
        h = h and before_ok and after_ok and ev
        holds = holds and h
        details.append(
            f"{n}: {det}; new Pods at first sync [{phases}]; in metrics API [{metric_times}]; "
            f"spec.replicas owner after operation [{owner_str(s_after) if s_after else '-'}]; after revert [{owner_str(back[3]) if back else '-'}]; "
            f"kubectl entry after revert {any(e['manager'] == 'kubectl' for e in back[3]['entries']) if back else '-'}; SuccessfulRescale New size 2 {ev}"
        )
    return missing, holds, "<br>".join(details)


def p2(d):
    m, h, det, info = held_then_reverted(d, "c2", WINDOW_C2)
    c1 = []
    for n in [x for x in d.names if x.startswith("c1-")]:
        op = d.op(n)
        rev = first_rescale(d.sync_points(n, op)) if op is not None else None
        if rev:
            c1.append(rev["t"] - op)
    rev = info.get("rev")
    diff = None
    if c1 and rev:
        diff = sum(c1) / len(c1) - (rev["t"] - info["op"])
    earlier = diff is not None and (WINDOW_DEFAULT - WINDOW_C2) - SYNC - JITTER <= diff <= (WINDOW_DEFAULT - WINDOW_C2) + SYNC + JITTER
    return m, info.get("reverted", False) and info.get("timed", False) and earlier, f"{det}; C1 operation -> revert {[f'{x:.1f}s' for x in c1]}; C1 mean minus C2 {f'{diff:.1f}s' if diff is not None else '-'}"


def reverted_at_first(d, name, want, reason=None):
    missing = basic_missing(d, name)
    op = d.op(name)
    if op is None:
        return missing, False, "no operation"
    pts = d.sync_points(name, op)
    if not pts:
        return missing, False, "no sync"
    first = pts[0]
    newp = d.new_pods(name, op)
    phases = ", ".join(f"{p} {d.phase_at(p, first['t'])}" for p in newp) or "-"
    ok = first["result"] is not None and first["result"][0] == "rescaled" and f"desired={want}" in first["result"][1]
    ev = [e for e in d.events.get(name, []) if "SuccessfulRescale" in e]
    if reason:
        ok = ok and reason in first["result"][1] and any(reason in e for e in ev)
    chs = d.changes(name)
    seen = next((c[1] for c in chs if c[2].endswith(f"-> {want}")), "-")
    return missing, ok, (
        f"op {d.sec_of(name, 'operation')['t']}; first sync {first['raw']} proposing [{first['proposing']}] result {first['result']} AbleToScale {first['able']}; "
        f"spec.replicas -> {want} seen {seen}; new Pods at first sync [{phases}]; SuccessfulRescale {ev}"
    )


def p3(d):
    return reverted_at_first(d, "c3", 2)


def p4(d):
    return reverted_at_first(d, "c4", 2)


def p5(d):
    return reverted_at_first(d, "c5", 2, "below Spec.MinReplicas")


def p6(d):
    return reverted_at_first(d, "c6", 5, "above Spec.MaxReplicas")


def p7(d):
    m, h, det, info = held_then_reverted(d, "c7", WINDOW_DEFAULT)
    ss = d.sec_of("c7", "server-side")
    if ss is None:
        m.append("c7: no server-side section")
        return m, False, det
    cs = cmds(ss["body"])
    if not cs:
        m.append("c7: server-side apply not run")
        return m, False, det
    cmd, out, code = cs[0]
    s_ss = snapshot(ss["body"].split("[exit", 1)[1].split("\n", 1)[1] if "[exit" in ss["body"] else "")
    end = d.sec_of("c7", "end")
    s_end = snapshot(end["body"]) if end else None
    s_after = info.get("after")
    back = info.get("back")
    csa = bool(s_after) and s_after["replicas"] == "4" and [(e["manager"], e["operation"], e["subresource"]) for e in s_after["owners"]] == [("kubectl-client-side-apply", "Update", "-")]
    kcm = bool(back) and [(e["manager"], e["operation"], e["subresource"]) for e in back[3]["owners"]] == [("kube-controller-manager", "Update", "scale")]
    conflict = code != 0 and 'conflict with "kube-controller-manager"' in out and ".spec.replicas" in out
    unchanged = s_ss["replicas"] == "2" and bool(s_end) and s_end["replicas"] == "2"
    return m, h and csa and kcm and conflict and unchanged, (
        f"{det}; spec.replicas owner after client-side apply [{owner_str(s_after) if s_after else '-'}]; after revert [{owner_str(back[3]) if back else '-'}]; "
        f"server-side {ss['t']} exit {code}: {' '.join(out.split())}; spec.replicas after server-side {s_ss['replicas']}, at end {s_end['replicas'] if s_end else '-'}"
    )


def p8(d):
    m = basic_missing(d, "c8")
    chs = d.changes("c8")
    after = d.sec_of("c8", "after operation")
    end = d.sec_of("c8", "end")
    s_after = snapshot(after["body"]) if after else None
    final = snapshot(end["body"])["replicas"] if end else None
    to4 = s_after if s_after and s_after["replicas"] == "4" else next((c[3] for c in chs if c[2].endswith("-> 4")), None)
    away = [c for c in chs if not c[2].endswith("-> 4")]
    return m, bool(to4) and not away and final == "4", (
        f"after operation {s_after['replicas'] if s_after else '-'} owner [{owner_str(s_after) if s_after else '-'}]; changes {[(c[1], c[2]) for c in chs]}; "
        f"spec.replicas at end {final}"
    )


def p9(d):
    m = basic_missing(d, "c9")
    chs = d.changes("c9")
    after = d.sec_of("c9", "after operation")
    end = d.sec_of("c9", "end")
    a = snapshot(after["body"])["replicas"] if after else None
    final = snapshot(end["body"])["replicas"] if end else None
    op = d.op("c9")
    pts = d.sync_points("c9", op) if op is not None else []
    return m, a == "4" and not chs and final == "4", f"after operation {a}; changes {[(c[1], c[2]) for c in chs]}; at end {final}; AbleToScale at syncs {sorted({p['able'] for p in pts})}; proposals {sorted({p['proposing'] for p in pts if p['proposing']})}"


def p10(d):
    missing, holds, rows = [], True, []
    for n in d.names:
        before = d.sec_of(n, "before")
        after = d.sec_of(n, "after operation")
        if not before or not after:
            continue
        trans = []
        b, a = snapshot(before["body"])["replicas"], snapshot(after["body"])["replicas"]
        if a != b:
            trans.append((b, a, "operation"))
        for c in d.changes(n):
            x, y = c[2].replace("spec.replicas ", "").split(" -> ")
            trans.append((x, y, c[1]))
        for x, y, when in trans:
            hit = any("ScalingReplicaSet" in e and f" from {x} to {y}" in e for e in d.events.get(n, []))
            holds = holds and hit
            rows.append(f"{n} {x}->{y} ({when}) {hit}")
    if not d.events:
        missing.append("events.txt empty")
    return missing, holds and bool(rows), "; ".join(rows)


def p11(d):
    name = "c10"
    missing = basic_missing(d, name)
    op = d.op(name)
    if op is None:
        return missing, False, "no operation"
    pts = d.sync_points(name, op)
    if not pts:
        return missing, False, "no sync"
    newp = d.new_pods(name, op)
    if not newp:
        missing.append(f"{name}: no new Pod after the operation")
    rev = next((p for p in pts[1:] if p["result"] and p["result"][0] == "rescaled"), None)
    gone_by = rev["t"] if rev else float("inf")
    remaining = [p for p in newp if not any(q == p and kv.get("deleting") == "True" and t < gone_by for t, q, kv in d.pods)]
    unseen = [p for p in remaining if p not in d.metrics]
    if unseen:
        missing.append(f"{name}: new Pods never in the metrics API: {unseen}")
    seen = [d.metrics[p][0] for p in remaining if p in d.metrics]
    mt = max(seen) if remaining and not unseen else None
    mraw = max((d.metrics[p][1] for p in remaining if p in d.metrics), default="-")
    first = pts[0]
    to3 = first["result"] is not None and first["result"][0] == "rescaled" and "desired=3" in first["result"][1]
    with_metrics = [p for p in pts if mt is not None and p["t"] > mt and (rev is None or p["t"] < rev["t"])]
    kept = bool(with_metrics) and all(p["result"] == ("decided", "desired=3") and p["able"] == "True/ScaleDownStabilized" for p in with_metrics)
    reverted = bool(rev) and "desired=2" in rev["result"][1]
    threes = [p for p in pts if p["proposing"] is not None and p["proposing"].startswith("desired=3 ")]
    last3 = threes[-1] if threes else None
    delta = rev["t"] - last3["t"] if rev and last3 else None
    timed = delta is not None and WINDOW_DEFAULT - JITTER <= delta <= WINDOW_DEFAULT + SYNC + JITTER
    chs = d.changes(name)
    phases = ", ".join(f"{p} {d.phase_at(p, first['t'])}" for p in newp)
    ev = [e for e in d.events.get(name, []) if "SuccessfulRescale" in e]
    before_rev = [p for p in pts if rev is None or p["t"] < rev["t"]]
    return missing, to3 and kept and reverted and timed, (
        f"op {d.sec_of(name, 'operation')['t']}; new Pods at first sync [{phases}]; remaining new Pods all in metrics API {mraw}; "
        f"syncs until revert [{', '.join(map(sync_str, before_rev))}]; "
        f"AbleToScale after metrics until revert {sorted({p['able'] for p in with_metrics})}; "
        f"last proposing 3 {last3['raw'] if last3 else '-'}; revert sync {rev['raw'] if rev else '-'} {rev['result'][1] if rev else ''}; "
        f"last proposing 3 -> revert {f'{delta:.1f}s' if delta is not None else '-'}; spec.replicas changes {[(c[1], c[2]) for c in chs]}; SuccessfulRescale {ev}; "
        f"first sync to 3 {to3}, 3 kept with ScaleDownStabilized after metrics {kept}, reverted to 2 {reverted}, last proposing 3 -> revert in range {timed}"
    )


CHECKS = [("P1", p1), ("P2", p2), ("P3", p3), ("P4", p4), ("P5", p5), ("P6", p6), ("P7", p7), ("P8", p8), ("P9", p9), ("P10", p10), ("P11", p11)]


def evaluate(results, names):
    d = Data(results, names)
    rows = []
    for name, fn in CHECKS:
        try:
            missing, holds, detail = fn(d)
        except Exception as e:
            missing, holds, detail = [f"check failed: {type(e).__name__}: {e}"], False, "-"
        rows.append((name, missing, holds, detail))
    return rows
