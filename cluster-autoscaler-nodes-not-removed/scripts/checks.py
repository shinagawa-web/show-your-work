import os
import re
import sys

KLOG = re.compile(r'^([IWEF])\d{4} (\d\d):(\d\d):(\d\d\.\d+)\s+\d+ [^\]]+\] (.*)$')
PATTERNS = {
    "above": re.compile(r'Node (\S+) unremovable: (\w+) requested \(([\d.]+)% of allocatable\) is above the scale-down utilization threshold'),
    "below": re.compile(r'Node (\S+) - (\w+) requested is ([\d.]+)% of allocatable'),
    "minsize": re.compile(r'Skipping (\S+) - node group min size reached'),
    "noscaledown": re.compile(r'Skipping (\S+) from delete consideration - the node is marked as no scale down'),
    "cannot": re.compile(r'[Nn]ode (\S+) cannot be removed: (.*)'),
    "notsuitable": re.compile(r'[Nn]ode (\S+) is not suitable for removal: (.*)'),
    "may": re.compile(r'[Nn]ode (\S+) may be removed'),
    "unneeded": re.compile(r'^(\S+) was unneeded for (\S+)'),
    "ignoring": re.compile(r'Scale-down calculation: ignoring (\d+) nodes unremovable in the last (\S+)'),
    "status": re.compile(r'Scale down status: .*scaleDownForbidden=(\w+) scaleDownInCooldown=(\w+)'),
    "removing": re.compile(r'Scale-down: removing (?:empty )?node "?([^",\s]+)'),
}
NG_A = ["ng-a-0", "ng-a-1", "ng-a-2"]
RECHECK = 60.0
BSP = 60.0
CHANGES = {
    "base": "Baseline: CPU and memory requests of web, api and worker lowered",
    "p1": "A fourth node, providerID kwok, whose node group label (ng-b) matches no template",
    "p2": "Node group min size set to the current node count (3)",
    "p3": "ng-a-0 annotated cluster-autoscaler.kubernetes.io/scale-down-disabled=true",
    "p4": "Only the memory requests lowered",
    "p5": "web and api have a sidecar (restartPolicy: Always init container) with requests",
    "p6": "Pods without a controller on ng-a-0 and ng-a-2, a Pod owned by a custom resource on ng-a-1",
    "p7": "A kube-system Deployment without a PDB (one Pod per node), created right before lowering",
    "p8": "p7 with cluster-autoscaler 1.32.1 (and without --blocking-system-pod-distruption-timeout, which 1.32.1 lacks)",
    "p9": "A Deployment annotated safe-to-evict: \"false\" (one Pod per node)",
    "p10": "metrics-server as the chart renders it (kube-system, no PDB, emptyDir), one Pod per node",
    "p11": "p10 with a PDB (maxUnavailable: 1)",
    "p12": "p11 with --skip-nodes-with-local-storage=false",
    "p13": "A Deployment with an emptyDir of medium: Memory (one Pod per node)",
    "p14": "A Deployment with a hostPath volume (one Pod per node)",
    "p15": "Three replicas: 1 Deployments, each with a minAvailable: 1 PDB (one Pod per node)",
    "p16": "A Deployment with required podAntiAffinity on hostname, replicas equal to the node count",
    "p17": "p16 plus a Pod without a controller on ng-a-0",
    "p19": "p4 plus p15; after observing, the CPU requests are lowered too",
}


def secs(h, m, s):
    return int(h) * 3600 + int(m) * 60 + float(s)


def fmt(t):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    return f"{h:02d}:{m:02d}:{t % 60:09.6f}"


class Run:
    def __init__(self, d):
        self.d = d
        self.marks = []
        for line in self.read("events.log").splitlines():
            ts, _, text = line.partition(" ")
            h, m, s = ts.split(":")
            self.marks.append((secs(h, m, s), text))
        self.lines = []
        log = self.read("ca.log") or self.read("ca-stream.log")
        for raw in log.splitlines():
            m = KLOG.match(raw)
            if m:
                self.lines.append((secs(m.group(2), m.group(3), m.group(4)), m.group(5), raw))
        self.nodes_log = self.read("nodes.log").splitlines()

    def read(self, name):
        p = os.path.join(self.d, name)
        return open(p).read() if os.path.exists(p) else ""

    def mark(self, prefix):
        for t, text in self.marks:
            if text.startswith(prefix):
                return t
        return None

    def find(self, kind, start=0.0, end=1e9, node=None):
        out = []
        for t, msg, raw in self.lines:
            if not start <= t <= end:
                continue
            m = PATTERNS[kind].search(msg)
            if m and (node is None or m.group(1) == node):
                out.append((t, m, raw))
        return out

    def counts(self):
        out = []
        for line in self.nodes_log:
            parts = line.split()
            h, m, s = parts[0].split(":")
            out.append((secs(h, m, s), int(parts[1])))
        return out

    def created(self, name_prefix):
        out = {}
        for line in self.read("pods-pre.txt").splitlines()[1:]:
            f = line.split()
            if len(f) >= 4 and f[1].startswith(name_prefix):
                h, m, s = f[3].split("T")[1].rstrip("Z").split(":")
                out[f[2]] = secs(h, m, s)
        return out

    def promql(self, name):
        out = {}
        for line in self.read(f"promql-{name}.txt").splitlines():
            f = line.split()
            if len(f) == 7 and f[0].startswith("ng-"):
                out[(f[0], f[1])] = (float(f[5]), float(f[6]), float(f[3]), float(f[4]))
        return out

    def pdb(self, name):
        out = {}
        sect = False
        for line in self.read(f"promql-{name}.txt").splitlines():
            if line.startswith("kube_poddisruptionbudget"):
                sect = True
                continue
            if sect and line.strip():
                k, v = line.split()
                out[k] = float(v)
        return out


class Report:
    def __init__(self):
        self.rows = []
        self.evidence = []

    def claim(self, text, held, evidence):
        self.rows.append((text, held))
        self.evidence.append(evidence)


def first_per_node(run, kind, nodes, start, end=1e9):
    lines = []
    for n in nodes:
        f = run.find(kind, start, end, n)
        lines.append(f[0][2] if f else f"(no line for {n})")
    return lines, all(run.find(kind, start, end, n) for n in nodes)


def none_for(run, kinds, nodes, start):
    hits = [raw for k in kinds for n in nodes for _, _, raw in run.find(k, start, node=n)]
    return hits


def count_change(run):
    c = run.counts()
    return (c[0][1], c[-1][1]) if c else (None, None)


def decrease_time(run):
    c = run.counts()
    for t, n in c[1:]:
        if n < c[0][1]:
            return t
    return None


def stopped_claim(rep, run, nodes, start):
    hits = none_for(run, ["may", "unneeded"], nodes, start)
    rep.claim(f"No `may be removed` / `was unneeded for` line for {', '.join(nodes)} after lowering", not hits, hits[:5] or ["(none)"])


def unchanged_claim(rep, run):
    a, b = count_change(run)
    rep.claim("Node count unchanged", a is not None and a == b, run.nodes_log)


def decreased_claim(rep, run, after=None, label="Node count decreases by one"):
    a, b = count_change(run)
    t = decrease_time(run)
    ok = a is not None and b == a - 1 and (after is None or (t is not None and t > after))
    ev = list(run.nodes_log)
    if after is not None:
        ev.append(f"(timeout passes at {fmt(after)})")
    rep.claim(label, ok, ev)


def bsp_claims(rep, run, kind_pattern, prefix, before_expected, after_kind):
    created = run.created(prefix)
    lower = run.mark("lower start")
    lines = [(t, m, raw) for t, m, raw in run.find("cannot", lower) if kind_pattern in m.group(2)]
    ev = [f"{n}: Pod created {fmt(c)}, timeout passes {fmt(c + BSP)}" for n, c in sorted(created.items())]
    before = [raw for t, m, raw in lines if t < created.get(m.group(1), 0) + BSP]
    after = [raw for t, m, raw in lines if t > created.get(m.group(1), 1e9) + BSP]
    rep.claim(f"Before the timeout: `{kind_pattern}`", bool(before) == before_expected, ev + (before[:3] or ["(no line before the timeout)"]))
    return created, after


def check(cond, run):
    rep = Report()
    before_start = run.mark("ca started") or 0
    before_end = run.mark("before checked") or 0
    lower = run.mark("lower start") or 0
    lowered = run.mark("lower done") or 0
    if cond == "base":
        lines, ok = first_per_node(run, "above", NG_A, before_start, before_end)
        rep.claim("Before lowering, every node has a utilization line", ok, lines)
        may = run.find("may", lower)
        un = run.find("unneeded", lower)
        rep.claim("After lowering, `may be removed` and `was unneeded for` appear", bool(may) and bool(un), [x[2] for x in may[:3] + un[:3]])
        decreased_claim(rep, run)
        st = run.find("status", lowered, decrease_time(run) or 1e9)
        combos = {}
        for _, m, raw in st:
            combos.setdefault((m.group(1), m.group(2)), raw)
        rep.claim("`Scale down status:` lines read `scaleDownForbidden=false scaleDownInCooldown=false` until the removal", bool(st) and list(combos) == [("false", "false")], [f"{len(st)} lines; distinct values:"] + list(combos.values()))
    elif cond == "p1":
        span = (run.marks[-1][0] if run.marks else 0) - lowered
        kinds = ["above", "below", "minsize", "noscaledown", "cannot", "notsuitable", "may", "unneeded"]
        hits = [raw for k in kinds for _, _, raw in run.find(k, node="ng-b-0")]
        rep.claim(f"No line on whether ng-b-0 can be removed, over {span:.0f}s after lowering (recheck timeout {RECHECK:.0f}s)", not hits and span > RECHECK, hits[:10] or ["(none)"])
        other = [raw for t, msg, raw in run.lines if "ng-b-0" in msg and raw not in hits]
        rep.claim("Other lines that mention ng-b-0 (for reference)", True, [f"{len(other)} lines"] + other[:10])
        stopped_claim(rep, run, ["ng-b-0"], lower)
        rep.claim("Node count", True, run.nodes_log)
    elif cond == "p2":
        first_status = run.find("status")
        t0 = first_status[0][0] if first_status else 0
        lines, ok = first_per_node(run, "minsize", NG_A, 0, t0)
        rep.claim("From the first loop, `node group min size reached (current: N, min: M)` for every node", ok and all("(current:" in l for l in lines), lines)
        util = [x[2] for k in ("above", "below") for n in NG_A for x in run.find(k, node=n)]
        rep.claim("No utilization line for any node", not util, util[:5] or ["(none)"])
        stopped_claim(rep, run, NG_A, lower)
        unchanged_claim(rep, run)
    elif cond == "p3":
        lines, ok = first_per_node(run, "noscaledown", ["ng-a-0"], lowered)
        rep.claim("`marked as no scale down` for ng-a-0", ok, lines)
        stopped_claim(rep, run, ["ng-a-0"], lower)
        rep.claim("Node count", True, run.nodes_log)
    elif cond in ("p4", "p5"):
        lines, ok = first_per_node(run, "above", NG_A, lowered)
        if cond == "p4":
            ok = ok and all("cpu requested" in l for l in lines)
            rep.claim("`cpu requested (NN% of allocatable) is above the scale-down utilization threshold` for every node", ok, lines)
        else:
            rep.claim("`above the scale-down utilization threshold` for every node", ok, lines)
        stopped_claim(rep, run, NG_A, lower)
        unchanged_claim(rep, run)
        p20(rep, run, cond, lowered)
    elif cond == "p6":
        lines, ok = first_per_node(run, "cannot", NG_A, lowered)
        ok = ok and all("is not replicated" in l for l in lines)
        rep.claim("`is not replicated` for the Pod without a controller (ng-a-0, ng-a-2) and the Pod owned by a custom resource (ng-a-1)", ok, lines)
        stopped_claim(rep, run, NG_A, lower)
        unchanged_claim(rep, run)
    elif cond in ("p7", "p8", "p10"):
        created, after = bsp_claims(rep, run, "non-pdb-assigned kube-system pod present", "metrics-server" if cond == "p10" else "cluster-agent", True, None)
        timeout = max(created.values()) + BSP if created else None
        if cond == "p8":
            rep.claim("After the timeout, `non-pdb-assigned kube-system pod present` continues", bool(after) and max(t for t, _, _ in run.find("cannot", lower)) > timeout + RECHECK, after[-3:] or ["(no line after the timeout)"])
            stopped_claim(rep, run, NG_A, lower)
            unchanged_claim(rep, run)
        else:
            decreased_claim(rep, run, timeout, "After the timeout, the node count decreases by one")
            if cond == "p10":
                ls = [raw for _, m, raw in run.find("cannot", lower) if "pod with local storage present" in m.group(2)]
                rep.claim("No `pod with local storage present` line", not ls, ls[:3] or ["(none)"])
    elif cond in ("p11", "p12"):
        created = run.created("metrics-server")
        timeout = max(created.values()) + BSP if created else 0
        ls = [(t, raw) for t, m, raw in run.find("cannot", lower) if "pod with local storage present" in m.group(2)]
        ks = [raw for _, m, raw in run.find("cannot", lower) if "kube-system pod present" in m.group(2)]
        ev = [f"{n}: Pod created {fmt(c)}, timeout passes {fmt(c + BSP)}" for n, c in sorted(created.items())]
        if cond == "p11":
            b = [r for t, r in ls if t < timeout]
            a = [r for t, r in ls if t > timeout]
            rep.claim("`pod with local storage present` before the timeout", bool(b), ev + (b[:3] or ["(none before the timeout)"]))
            rep.claim("`pod with local storage present` after the timeout", bool(a), a[-3:] or ["(none after the timeout)"])
            rep.claim("No `non-pdb-assigned kube-system pod present` line", not ks, ks[:3] or ["(none)"])
            stopped_claim(rep, run, NG_A, lower)
            unchanged_claim(rep, run)
        else:
            rep.claim("No `pod with local storage present` line", not ls, [r for _, r in ls[:3]] or ["(none)"])
            decreased_claim(rep, run)
    elif cond == "p13":
        c = [raw for _, m, raw in run.find("cannot", lower) if "session-cache" in m.group(2) or "local storage" in m.group(2)]
        rep.claim("No `cannot be removed:` line for the Pods with the memory emptyDir", not c, c[:3] or ["(none)"])
        decreased_claim(rep, run)
    elif cond in ("p9", "p14", "p15", "p19"):
        text = {"p9": "pod annotated as not safe to evict present", "p14": "pod with local storage present", "p15": "not enough pod disruption budget to move", "p19": "not enough pod disruption budget to move"}[cond]
        start = lowered
        if cond == "p19":
            s1_end = run.mark("lower2 start") or 0
            lines, ok = first_per_node(run, "above", NG_A, lowered, s1_end)
            other = [raw for k in ("cannot", "notsuitable", "may") for _, _, raw in run.find(k, lowered, s1_end)]
            rep.claim("Before CPU is lowered: only utilization lines", ok and not other, lines + other[:3])
            start = run.mark("lower2 done") or 0
        lines, ok = first_per_node(run, "cannot", NG_A, start)
        ok = ok and all(text in l for l in lines)
        rep.claim(f"`{text}` for every node" + (" after CPU is lowered" if cond == "p19" else ""), ok, lines)
        if cond == "p15":
            vals = {k: v for name in ("after", "end") for k, v in run.pdb(name).items() if "single-" in k}
            allowed = [f"{name}: {k} {v:g}" for name in ("after", "end") for k, v in sorted(run.pdb(name).items()) if "single-" in k]
            rep.claim("`kube_poddisruptionbudget_status_pod_disruptions_allowed` stays 0", bool(vals) and all(v == 0 for v in vals.values()), allowed)
            p18(rep, run, lowered)
        if cond != "p19":
            stopped_claim(rep, run, NG_A, lower)
        unchanged_claim(rep, run)
    elif cond in ("p16", "p17"):
        if cond == "p16":
            lines, ok = first_per_node(run, "notsuitable", NG_A, lowered)
            ok = ok and all("can reschedule only" in l for l in lines)
            rep.claim("`is not suitable for removal: can reschedule only N out of M pods` for every node", ok, lines)
        else:
            c = run.find("cannot", lowered, node="ng-a-0")
            ns = run.find("notsuitable", lowered, node="ng-a-0")
            rep.claim("ng-a-0: `cannot be removed:` and no `is not suitable for removal:`", bool(c) and not ns, [x[2] for x in c[:1] + ns[:3]] or ["(no line)"])
            lines, _ = first_per_node(run, "notsuitable", ["ng-a-1", "ng-a-2"], lowered)
            rep.claim("Other nodes (for reference)", True, lines)
        stopped_claim(rep, run, NG_A, lower)
        unchanged_claim(rep, run)
    return rep


def p18(rep, run, lowered):
    ev = []
    ok = True
    for n in NG_A:
        c = run.find("cannot", lowered, node=n)
        if not c:
            ok = False
            ev.append(f"(no `cannot be removed:` for {n})")
            continue
        t1 = c[0][0]
        inside = [x for x in c if t1 < x[0] < t1 + RECHECK - 1]
        again = [x for x in c if x[0] >= t1 + RECHECK - 1]
        ign = run.find("ignoring", t1 + 1, t1 + RECHECK - 1)
        ok = ok and not inside and bool(again) and bool(ign)
        ev.append(f"{n}: first {c[0][2]}")
        ev.extend(f"{n}: within {RECHECK:.0f}s {x[2]}" for x in inside[:2])
        ev.append(f"{n}: `ignoring` lines within {RECHECK:.0f}s: {len(ign)}" + (f", first {ign[0][2]}" if ign else ""))
        ev.append(f"{n}: next {again[0][2]}" if again else f"{n}: (no line again)")
    rep.claim("P18: after the first `cannot be removed:`, no line for the node until the recheck timeout, `ignoring N nodes unremovable in the last` instead, then the same line again", ok, ev)


def p20(rep, run, cond, lowered):
    q = run.promql("after")
    ev = []
    ok = True
    for n in NG_A:
        f = run.find("above", lowered, node=n)
        if not f or (n, "cpu") not in q:
            ok = False
            ev.append(f"{n}: missing data")
            continue
        res, nn = f[-1][1].group(2), float(f[-1][1].group(3))
        cpu, mem = q[(n, "cpu")], q[(n, "memory")]
        ev.append(f"{n}: log {res} {nn:g}% | PromQL cpu {cpu[0]:g}% memory {mem[0]:g}% | init containers cpu {100 * cpu[2] / cpu[3]:g}% ({cpu[2]:g} of {cpu[3]:g} cores)")
        if cond == "p4":
            ok = ok and res == "cpu" and mem[0] < 50 <= nn
        else:
            ok = ok and abs(nn - cpu[0] - 100 * cpu[2] / cpu[3]) < 0.01 and cpu[0] < 50 <= nn
    text = ("P20: the log names cpu while the memory PromQL value is under 50%" if cond == "p4"
            else "P20: log NN minus the PromQL cpu value equals the sidecar requests / allocatable")
    rep.claim(text, ok, ev)


def main():
    root = sys.argv[1]
    conds = sys.argv[2:]
    out = ["# cluster-autoscaler-nodes-not-removed", ""]
    overview = ["| Condition | Change from the baseline | Predictions held |", "|---|---|---|"]
    detail = []
    invalid = False
    for c in conds:
        d = os.path.join(root, c)
        inv = os.path.join(d, "invalid.txt")
        if not os.path.isdir(d):
            invalid = True
            overview.append(f"| {c} | {CHANGES.get(c, '')} | RUN INVALID: no results |")
            continue
        if os.path.exists(inv):
            invalid = True
            overview.append(f"| {c} | {CHANGES.get(c, '')} | {open(inv).read().strip()} |")
            continue
        run = Run(d)
        if not run.find("status", run.mark("lower done") or 0):
            invalid = True
            overview.append(f"| {c} | {CHANGES.get(c, '')} | RUN INVALID: no scale-down loop after lowering in ca.log |")
            continue
        rep = check(c, run)
        held = sum(1 for _, h in rep.rows if h)
        overview.append(f"| {c} | {CHANGES.get(c, '')} | {held}/{len(rep.rows)} |")
        detail += [f"## {c}", "", CHANGES.get(c, ""), "", "| # | Prediction | Held |", "|---|---|---|"]
        detail += [f"| {i + 1} | {t} | {'yes' if h else 'no'} |" for i, (t, h) in enumerate(rep.rows)]
        detail.append("")
        for i, ev in enumerate(rep.evidence):
            detail += [f"{i + 1}.", "", "```"] + [str(e) for e in ev] + ["```", ""]
    if invalid:
        out.insert(0, "RUN INVALID: see the table")
    print("\n".join(out + overview + [""] + detail))


main()
