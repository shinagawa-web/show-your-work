import json
import re


def kv(block):
    return {k: int(v) for k, v in re.findall(r"^(\w[\w.]*) (\d+)$", block, re.M)}


def cmds(t):
    return [
        (m.group(1), m.group(2), int(m.group(3)))
        for m in re.finditer(r"^\$ (.*)\n((?:(?!\$ |\[exit \d+\]$).*\n)*)\[exit (\d+)\]$", t, re.M)
    ]


def find(cs, pattern):
    return next((c for c in cs if re.search(pattern, c[0])), None)


def need(missing, c, label, codes=(0,), output=True):
    if c is None:
        missing.append(f"{label} not run")
    elif c[2] not in codes:
        missing.append(f"{label} exited {c[2]}")
    elif output and not c[1].strip():
        missing.append(f"{label} printed nothing")


def p1(t):
    missing = []
    triples = 0
    ordered = 0
    pods = re.split(r"^## ", t, flags=re.M)[1:]
    if len(pods) != 2:
        missing.append(f"{len(pods)} Pods recorded, 2 expected")
    roots = []
    for pod in pods:
        name = pod.split("\n", 1)[0]
        cs = cmds(pod)
        need(missing, find(cs, r"^node cat /proc/\d*/cgroup$"), f"{name}: node cat /proc/<pid>/cgroup")
        inside = find(cs, r"^kubectl exec \w+ -- cat /proc/self/cgroup$")
        need(missing, inside, f"{name}: cat /proc/self/cgroup inside")
        if inside:
            roots.append(inside[1].strip())
        complete = 0
        for group in pod.split("\n\n"):
            parts = re.split(r"^(node|exec)\s+\S+\n", group, flags=re.M)
            vals = [kv(parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
            if len(vals) != 3 or not all("usage_usec" in v for v in vals):
                continue
            complete += 1
            a, b, c = vals
            if all(a[k] <= b.get(k, -1) <= c.get(k, -1) for k in a):
                ordered += 1
        if complete != 3:
            missing.append(f"{name}: {complete}/3 complete node, exec, node reads of cpu.stat")
        triples += complete
    holds = triples > 0 and ordered == triples and bool(roots) and all(r == "0::/" for r in roots)
    return missing, holds, f"node before <= exec <= node after in {ordered}/{triples} reads; /proc/self/cgroup inside: {roots}"


def p2(t):
    missing = []
    cs = cmds(t)
    need(missing, find(cs, r"cat /sys/fs/cgroup/cpu\.max$"), "cat cpu.max")
    stat = find(cs, r"cat /sys/fs/cgroup/cpu\.stat$")
    need(missing, stat, "cat cpu.stat")
    if stat and "usage_usec" not in kv(stat[1]):
        missing.append("cpu.stat has no usage_usec")
    v1 = find(cs, r"grep throttled_time ")
    v2 = find(cs, r"grep throttled_usec ")
    need(missing, v1, "grep throttled_time", codes=(0, 1), output=False)
    need(missing, v2, "grep throttled_usec", codes=(0, 1), output=False)
    usec = re.search(r"^throttled_usec (\d+)$", v2[1], re.M) if v2 else None
    holds = bool(v1 and v1[2] == 1 and usec)
    return missing, holds, f"grep throttled_time exit {v1[2] if v1 else '-'}; throttled_usec {usec.group(1) if usec else '-'}"


def p3(t):
    missing = []
    cs = cmds(t)
    need(missing, find(cs, r"^kubectl exec cache -- test -f /data/done$"), "test -f /data/done in the cache Pod", output=False)
    if not re.search(r"^container scope: /\S+\.scope$", t, re.M):
        missing.append("no container scope of the cache Pod")
    if not re.search(r"^pod slice: /\S+\.slice$", t, re.M):
        missing.append("no Pod slice of the cache Pod")
    if not re.search(r"^settled: yes$", t, re.M):
        missing.append("memory.current of the cache Pod did not settle")
    total = find(cs, r"^node grep -E \^total_ ")
    need(missing, total, "grep ^total_ memory.stat", codes=(0, 1), output=False)
    samples = re.split(r"^## sample \d+$", t, flags=re.M)[1:]
    rows = []
    for n, s in enumerate(samples, 1):
        both = re.findall(r"^memory\.current=(\d+) inactive_file=(\d+) ", s, re.M)
        calc = re.search(r"\.scope\n(?:.*\n)*?memory\.current-inactive_file=(\d+)", s)
        ws = re.search(r"^container_memory_working_set_bytes\{[^}]*\} (\S+)", s, re.M)
        if len(both) != 2 or not calc or not ws:
            missing.append(f"sample {n}: memory.current, inactive_file or container_memory_working_set_bytes missing")
            continue
        rows.append((int(calc.group(1)), int(float(ws.group(1))), int(both[0][1])))
    if len(samples) != 3:
        missing.append(f"{len(samples)}/3 samples")
    holds = bool(total and total[2] == 1 and rows and all(a == b and i > 0 for a, b, i in rows))
    return missing, holds, f"grep ^total_ exit {total[2] if total else '-'}; (current-inactive_file, working_set, inactive_file) {rows}"


def p4(t):
    missing = []
    if not re.search(r"^pod slice: /\S+\.slice$", t, re.M):
        missing.append("no Pod slice of the oom Pod")
    if re.search(r"^deadline reached$", t, re.M):
        missing.append("the oom Pod did not reach the restart count before the deadline")
    snaps = []
    for blk in re.split(r"^## (?=\d\d:)", t, flags=re.M)[1:]:
        head, _, body = blk.partition("\n")
        st = json.loads(head.split(" ", 1)[1])
        files = {}
        for m in re.finditer(r"^-- (\S+)/(memory\.[\w.]+)\n((?:.+\n)+?)(?=--|\Z|\n)", body + "\n", re.M):
            kind = "scope" if m.group(1).endswith(".scope") else "slice"
            files[(kind, m.group(2))] = kv(m.group(3))
        snaps.append((st, files))
    running = [(st, f) for st, f in snaps if st["state"] == "running" and st["restartCount"] >= 1]
    if not running:
        missing.append("no running snapshot after a restart")
    holds = bool(running)
    rows = []
    for st, f in running:
        n = st["restartCount"]
        scope = f.get(("scope", "memory.events"), {})
        slice_ = f.get(("slice", "memory.events"), {})
        local = f.get(("slice", "memory.events.local"), {})
        for label, d in [("container scope memory.events", scope), ("Pod slice memory.events", slice_), ("Pod slice memory.events.local", local)]:
            if not d:
                missing.append(f"restartCount {n}: {label} empty")
        good = (
            scope and not any(scope.values())
            and slice_.get("oom_group_kill") == n
            and local.get("oom_kill") == 0
        )
        holds = holds and bool(good)
        rows.append(f"restartCount {n}: scope events sum {sum(scope.values()) if scope else '-'}, slice oom_group_kill {slice_.get('oom_group_kill')}, slice oom_kill {slice_.get('oom_kill')}, slice local oom_kill {local.get('oom_kill')}")
    return missing, holds, "; ".join(rows) or "no running snapshot after a restart"


def p5(t):
    missing = []
    cs = cmds(t)
    slice_ = find(cs, r"\.slice/cpu\.weight$")
    scope = find(cs, r"\.scope/cpu\.weight$")
    runc = find(cs, r"^node runc --version$")
    need(missing, slice_, "node cat <Pod slice>/cpu.weight")
    need(missing, scope, "node cat <container scope>/cpu.weight")
    need(missing, runc, "node runc --version")
    need(missing, find(cs, r"^kubectl exec weight -- cat /sys/fs/cgroup/cpu\.weight$"), "cat cpu.weight inside")
    s1 = slice_[1].strip() if slice_ else "-"
    s2 = scope[1].strip() if scope else "-"
    ver = re.search(r"^runc version (\S+)", runc[1], re.M) if runc else None
    return missing, s1 == "39" and s2 == "100", f"Pod slice {s1}, container scope {s2}, runc {ver.group(1) if ver else '-'}"


def p6(t):
    missing = []
    pid = re.search(r"^weight container pid on the node: (\d+)$", t, re.M)
    if not pid:
        missing.append("no pid of the weight container")
    if not re.search(r"^weight container id from containerStatuses: [0-9a-f]{64}$", t, re.M):
        missing.append("no container id of the weight container")
    head, _, logs = t.partition("$ kubectl logs")
    hc = cmds(head)
    node = find(hc, r"^node cat /proc/\d*/cgroup$")
    need(missing, node, "node cat /proc/<pid>/cgroup")
    node_path = re.search(r"^0::(/\S*)$", node[1], re.M) if node else None
    if node and not node_path:
        missing.append("no 0:: line in /proc/<pid>/cgroup on the node")
    need(missing, find(hc, r"^kubectl debug node/"), "kubectl debug node", output=False)
    lc = cmds(logs)
    rel = find(lc, r"^cat /proc/\d*/cgroup$")
    need(missing, rel, "cat /proc/<pid>/cgroup in the debug Pod")
    ls = [c for c in lc if c[0].startswith("ls -d ")]
    if len(ls) != 2:
        missing.append(f"{len(ls)}/2 ls -d in the debug Pod")
    found = find(lc, r"^find /host/sys/fs/cgroup ")
    need(missing, found, "find in the debug Pod")
    procs = find(lc, r"/cgroup\.procs$")
    need(missing, procs, "cat cgroup.procs in the debug Pod")
    rel_line = rel[1].strip() if rel else ""
    found_path = found[1].strip().split("\n")[0] if found else ""
    found_rel = found_path[len("/host/sys/fs/cgroup"):] if found_path.startswith("/host/sys/fs/cgroup/") else None
    node_rel = node_path.group(1) if node_path else None
    pids = procs[1].split() if procs else []
    same_path = found_rel is not None and found_rel == node_rel
    has_pid = bool(pid) and pid.group(1) in pids
    holds = bool(
        rel_line.startswith("0::/..")
        and len(ls) == 2 and all(c[2] != 0 for c in ls)
        and same_path and has_pid
    )
    return missing, holds, (
        f"/proc/<pid>/cgroup {rel_line or '-'}; ls -d exits {[c[2] for c in ls]}; "
        f"find path without /host/sys/fs/cgroup same as 0:: path on the node: {same_path}; "
        f"pid {pid.group(1) if pid else '-'} in cgroup.procs {pids}: {has_pid}"
    )


CHECKS = [
    ("P1", "p1-cpu-stat-inside-vs-node.txt", p1),
    ("P2", "p2-throttled-key.txt", p2),
    ("P3", "p3-working-set.txt", p3),
    ("P4", "p4-oom-kill.txt", p4),
    ("P5", "p5-cpu-weight.txt", p5),
    ("P6", "p6-node-debug-cgroup.txt", p6),
]


def evaluate(results):
    rows = []
    for name, file, fn in CHECKS:
        f = results / file
        if not f.exists():
            rows.append((name, [f"{file} missing"], False, "-"))
            continue
        try:
            missing, holds, detail = fn(f.read_text())
        except Exception as e:
            missing, holds, detail = [f"check failed: {e}"], False, "-"
        rows.append((name, missing, holds, detail))
    return rows
