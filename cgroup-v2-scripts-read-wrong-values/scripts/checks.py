import json
import pathlib
import re
import sys

results = pathlib.Path(sys.argv[1])


def read(name):
    f = results / name
    return f.read_text() if f.exists() else ""


def kv(block):
    return {k: int(v) for k, v in re.findall(r"^(\w[\w.]*) (\d+)$", block, re.M)}


def p1():
    t = read("p1-cpu-stat-inside-vs-node.txt")
    triples = 0
    ordered = 0
    for pod in re.split(r"^## ", t, flags=re.M)[1:]:
        for group in pod.split("\n\n"):
            parts = re.split(r"^(node|exec)\s+\S+\n", group, flags=re.M)
            vals = [kv(parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
            if len(vals) != 3 or not vals[0]:
                continue
            triples += 1
            a, b, c = vals
            if all(a[k] <= b[k] <= c[k] for k in a):
                ordered += 1
    root = re.findall(r"^\$ kubectl exec \w+ -- cat /proc/self/cgroup\n(.*)$", t, re.M)
    ok = triples > 0 and ordered == triples and root and all(r == "0::/" for r in root)
    return ok, f"node before <= exec <= node after in {ordered}/{triples} reads; /proc/self/cgroup inside: {root}"


def p2():
    t = read("p2-throttled-key.txt")
    v1 = re.search(r"grep throttled_time .*\n(.*\n)?\[exit (\d+)\]", t)
    v2 = re.search(r"grep throttled_usec .*\nthrottled_usec (\d+)$", t, re.M)
    ok = bool(v1 and v1.group(2) == "1" and v2)
    return ok, f"grep throttled_time exit {v1.group(2) if v1 else '-'}; throttled_usec {v2.group(1) if v2 else '-'}"


def p3():
    t = read("p3-working-set.txt")
    total = re.search(r"grep -E \^total_ .*\n\[exit (\d+)\]", t)
    samples = re.split(r"^## sample \d+$", t, flags=re.M)[1:]
    rows = []
    for s in samples:
        calc = re.search(r"\.scope\n(?:.*\n)*?memory\.current-inactive_file=(\d+)", s)
        inact = re.search(r"\.scope\n(?:.*\n)*?inactive_file (\d+)", s)
        ws = re.search(r'^container_memory_working_set_bytes\{[^}]*\} (\S+)', s, re.M)
        if calc and ws and inact:
            rows.append((int(calc.group(1)), int(float(ws.group(1))), int(inact.group(1))))
    ok = bool(total and total.group(1) == "1" and rows and all(a == b and i > 0 for a, b, i in rows))
    return ok, f"grep ^total_ exit {total.group(1) if total else '-'}; (current-inactive_file, working_set, inactive_file) {rows}"


def p4():
    t = read("p4-oom-kill.txt")
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
    ok = bool(running) and "deadline reached" not in t
    rows = []
    for st, f in running:
        n = st["restartCount"]
        scope = f.get(("scope", "memory.events"), {})
        slice_ = f.get(("slice", "memory.events"), {})
        local = f.get(("slice", "memory.events.local"), {})
        good = (
            scope and not any(scope.values())
            and slice_.get("oom_group_kill") == n
            and local.get("oom_kill") == 0
        )
        ok = ok and bool(good)
        rows.append(f"restartCount {n}: scope events sum {sum(scope.values()) if scope else '-'}, slice oom_group_kill {slice_.get('oom_group_kill')}, slice oom_kill {slice_.get('oom_kill')}, slice local oom_kill {local.get('oom_kill')}")
    return ok, "; ".join(rows) or "no running snapshot after a restart"


def p5():
    t = read("p5-cpu-weight.txt")
    slice_ = re.search(r"\.slice/cpu\.weight\n(\d+)", t)
    scope = re.search(r"\.scope/cpu\.weight\n(\d+)", t)
    runc = re.search(r"^runc version (\S+)", t, re.M)
    s1, s2 = (slice_.group(1) if slice_ else "-"), (scope.group(1) if scope else "-")
    return s1 == "39" and s2 == "100", f"Pod slice {s1}, container scope {s2}, runc {runc.group(1) if runc else '-'}"


def p6():
    t = read("p6-node-debug-cgroup.txt")
    logs = t.split("$ kubectl logs", 1)[-1]
    rel = re.search(r"^\$ cat /proc/\d+/cgroup\n(.*)$", logs, re.M)
    ls = re.findall(r"^\$ ls -d (/\S+)\n(?:.*\n)*?\[exit (\d+)\]", logs, re.M)
    found = re.search(r"^\$ find .*\n(/\S+)\n\[exit 0\]", logs, re.M)
    weight = re.search(r"^\$ cat \S+/cpu\.weight\n(.*)\n\[exit (\d+)\]", logs, re.M)
    events = re.search(r"^\$ cat \S+/memory\.events\n((?:.+\n)+?)\[exit (\d+)\]", logs, re.M)
    node_w = re.search(r"^\$ node cat \S+/cpu\.weight\n(.*)$", t, re.M)
    node_e = re.search(r"^\$ node cat \S+/memory\.events\n((?:.+\n)+?)\[exit", t, re.M)
    ok = bool(
        rel and rel.group(1).startswith("0::/..")
        and len(ls) == 2 and all(code != "0" for _, code in ls)
        and found and weight and weight.group(2) == "0" and events and events.group(2) == "0"
        and node_w and weight.group(1) == node_w.group(1)
        and node_e and kv(events.group(1)) == kv(node_e.group(1))
    )
    return ok, (
        f"/proc/<pid>/cgroup {rel.group(1) if rel else '-'}; ls -d exits {[c for _, c in ls]}; "
        f"find {'found' if found else 'not found'}; cpu.weight {weight.group(1) if weight else '-'} (node {node_w.group(1) if node_w else '-'}); "
        f"memory.events same as node: {bool(events and node_e and kv(events.group(1)) == kv(node_e.group(1)))}"
    )


print("| | holds | observed |")
print("|---|---|---|")
for name, fn in [("P1", p1), ("P2", p2), ("P3", p3), ("P4", p4), ("P5", p5), ("P6", p6)]:
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"check failed: {e}"
    print(f"| {name} | {'yes' if ok else 'no'} | {detail} |")
