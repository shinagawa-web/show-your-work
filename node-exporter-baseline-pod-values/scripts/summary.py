import pathlib
import re
import sys

args = sys.argv[1:]
merged = args[0] == "--merged"
if merged:
    args = args[1:]
results = pathlib.Path(args[0])
sections = args[1].split()
invalid = list(args[2:])
S2 = "base h1 h2 r1 v1 b1".split()
ALERT_ROWS = "base h1 v1 b1".split()
WARN_ROWS = "h1 v1 b1".split()
S3_SUPPORT = "h1 r1 v1 b1".split()
FIELDS = ("hostNetwork: true", "hostPID: true", "path: /", "path: /proc", "path: /sys")
TITLES = {
    "s1": "Opening",
    "s2": "What happens",
    "s3": "Telling which state your DaemonSet is in",
    "s4": "Countermeasure",
}


def text(name, section):
    f = (results / section if merged else results) / name
    if not f.exists():
        invalid.append(f"{section}: {name} is missing")
        return ""
    return f.read_text()


def table(src, head):
    rows = {}
    on = False
    for line in src.splitlines():
        if line.startswith(head):
            on = True
            continue
        if on and line.startswith("|---"):
            continue
        if on and line.startswith("| "):
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows[cells[0]] = cells[1:]
        elif on:
            break
    return rows


def dry_run(src, ns):
    m = re.search(r"^\$ kubectl label --dry-run=server --overwrite ns " + re.escape(ns) + r" .*?\n(.*?)^\[exit (\d+)\]", src, re.M | re.S)
    return m.group(1).rstrip() if m else None


def mountlist(x):
    return ", ".join(f"`{m}`" for m in x.split(",")) if x and x != "-" else "none"


def block(body):
    return "```\n" + body + "\n```\n"


def pod_spec_lines(src):
    lines = src.splitlines()
    try:
        t = next(i for i, l in enumerate(lines) if l == "  template:")
        s = next(i for i in range(t + 1, len(lines)) if lines[i] == "    spec:")
    except StopIteration:
        return None, []
    end = next((i for i in range(s + 1, len(lines)) if lines[i] and not lines[i].startswith("     ")), len(lines))
    keep = []
    i = s + 1
    while i < end:
        l = lines[i]
        if re.match(r"^      (hostNetwork|hostPID):", l):
            keep.append(i)
        elif l == "      volumes:" or l.startswith("      volumes: "):
            keep.append(i)
            i += 1
            while i < end and (lines[i].startswith("       ") or lines[i].startswith("      - ")):
                keep.append(i)
                i += 1
            continue
        i += 1
    return lines, keep


def excerpt(lines, keep):
    if not keep:
        return "      ...\n      (no hostNetwork, hostPID or volumes line under spec.template.spec)\n      ..."
    out, prev = [], None
    for i in keep:
        if prev is None or i != prev + 1:
            out.append("      ...")
        out.append(lines[i])
        prev = i
    out.append("      ...")
    return "\n".join(out)


if merged:
    present = []
    for s in sections:
        own = results / s / "summary.md"
        if not own.exists():
            invalid.append(f"{s}: job results are missing")
            continue
        present.append(s)
        for l in own.read_text().splitlines():
            if l.startswith("RUN INVALID: "):
                x = l[len("RUN INVALID: "):]
                invalid.append(x if x.startswith(f"{s}: ") else f"{s}: {x}")
    sections = present

out = []
late = []

if "s1" in sections:
    d = dry_run(text("dryrun.txt", "s1"), "ne-base")
    if d is None:
        invalid.append("s1: no dry-run output of ne-base in dryrun.txt")
    out.append(f"## s1: {TITLES['s1']} (job `s1`)\n")
    out.append("`kubectl label --dry-run=server --overwrite ns ne-base pod-security.kubernetes.io/enforce=baseline` on the chart default\n")
    out.append(block(d or "(missing)"))

if "s2" in sections:
    cells_src = text("cells.txt", "s2")
    labels_src = text("labels.txt", "s2")
    cells_rows = table(cells_src, "| row |")
    labels_rows = table(labels_src, "| condition | pod IP |")
    checked = {}
    meaning = {}
    for src in (cells_src, labels_src):
        for k, v in table(src, "| series group | checked with |").items():
            checked.setdefault(k, v[0])
        for k, v in table(src, "| value | meaning |").items():
            meaning.setdefault(k, v[0])
    fs_seen = table(cells_src, "| condition | filesystem |")
    node_mounts = re.search(r"^node mountpoints \(.*?\): (.*)$", cells_src, re.M)
    alert_src = text("alert.txt", "s2")
    alert_rows = table(alert_src, "| condition | entries |")
    dry = text("dryrun.txt", "s2")
    if not cells_rows:
        invalid.append("s2: no verdict table in cells.txt")
    for c in S2:
        if c not in labels_rows:
            invalid.append(f"s2: no instance of variant {c} in labels.txt")
        if c not in alert_rows:
            invalid.append(f"s2: no alert expression result of variant {c} in alert.txt")
        elif "-" in alert_rows[c][:2]:
            invalid.append(f"s2: Prometheus returned no node_nf_conntrack_entries or node_nf_conntrack_entries_limit for variant {c} (alert.txt)")
    out.append(f"## s2: {TITLES['s2']} (job `s2`)\n")
    out.append("### Whose value each series group reports\n")
    out.append("- base: the chart default")
    out.append("- h1: `hostNetwork: false`")
    out.append("- h2: `hostPID: false`")
    out.append("- r1: `hostRootFsMount.enabled: false`")
    out.append("- v1: all three of h1, h2 and r1")
    out.append("- b1: v1 with the `/proc` and `/sys` hostPath volumes removed\n")
    out.append("| series group | " + " | ".join(S2) + " |")
    out.append("|---|" + "---|" * len(S2))
    for row, vals in cells_rows.items():
        out.append(f"| {row} | " + " | ".join(vals) + " |")
    instance = [labels_rows.get(c, ["-"] * 4)[2] for c in S2]
    out.append("| instance | " + " | ".join(instance) + " |")
    out.append("")
    out.append("Series checked for each group:\n")
    for row in [*cells_rows, "instance"]:
        if row not in checked:
            invalid.append(f"s2: no series listed for group {row} in cells.txt or labels.txt")
        out.append(f"- {row}: {checked.get(row, '(missing)')}")
    out.append("")
    seen = [v for vals in cells_rows.values() for v in vals] + instance
    keys = {v.split("(", 1)[0] for v in seen}
    for k in sorted(keys - set(meaning)):
        invalid.append(f"s2: no meaning for cell value {k} in cells.txt or labels.txt")
    out.append("Cell values:\n")
    for k, v in meaning.items():
        if k in keys:
            out.append(f"- {k}: {v}")
    out.append("")
    shown = [c for c in S2 if fs_seen.get(c, [""])[0] in ("partial-error", "container-list")]
    if shown:
        if not node_mounts:
            invalid.append("s2: no node mountpoints in cells.txt")
        out.append("### Filesystem mountpoints behind partial-error and container-list\n")
        out.append("From `node_filesystem_device_error` and `node_filesystem_size_bytes` on each variant's `/metrics`. The node's own mountpoints were " + (mountlist(node_mounts.group(1)) if node_mounts else "(missing)") + ".\n")
        for c in shown:
            fv, listed, err1, nosize, cont = fs_seen[c]
            if fv == "partial-error":
                out.append(f"- {c} (partial-error): mountpoints {mountlist(listed)}; node_filesystem_device_error 1 on {mountlist(err1)}; no node_filesystem_size_bytes on {mountlist(nosize)}")
            else:
                out.append(f"- {c} (container-list): mountpoints {mountlist(listed)}; the container's own mountpoints {mountlist(cont)}")
        out.append("")
    out.append("### Conntrack alert expression\n")
    out.append("`(node_nf_conntrack_entries{job=\"node-exporter\"} / node_nf_conntrack_entries_limit) > 0.75`, queried while the node's conntrack table is filled above the threshold.\n")
    out.append("| variant | node_nf_conntrack_entries | node_nf_conntrack_entries_limit | expression |")
    out.append("|---|---|---|---|")
    for c in ALERT_ROWS:
        v = alert_rows.get(c, ["-"] * 4)
        out.append(f"| {c} | {v[0]} | {v[1]} | {v[3].split()[0]} |")
    out.append("")
    out.append("returned means the expression has a result, so the alert fires; empty means it has no result, so the alert does not fire.\n")

    late.append("### s2: Pod warning line of the Baseline dry-run\n")
    lines = []
    for c in WARN_ROWS:
        d = dry_run(dry, f"ne-{c}")
        if d is None:
            invalid.append(f"s2: no dry-run output of variant {c} in dryrun.txt")
            continue
        warn = [l for l in d.splitlines() if l.startswith("Warning: ") and not l.startswith("Warning: existing pods")]
        if warn:
            lines += [f"{c}: {w}" for w in warn]
        elif not any(l.startswith("Warning") for l in d.splitlines()):
            lines.append(f"{c}: no Warning line ({d.splitlines()[-1]})")
        else:
            invalid.append(f"s2: no Pod warning line of variant {c} in dryrun.txt")
    late.append(block("\n".join(lines) or "(missing)"))
    m = re.match(r"node nf_conntrack_count=(\d+) nf_conntrack_max=(\d+)", alert_src)
    if not m:
        invalid.append("s2: no node conntrack count and limit in alert.txt")
    late.append("### s2: node conntrack when the expression was queried\n")
    late.append(block(f"nf_conntrack_count={m.group(1)} nf_conntrack_max={m.group(2)}" if m else "(missing)"))

if "s3" in sections:
    out.append(f"## s3: {TITLES['s3']} (job `s3`)\n")
    out.append("`kubectl get daemonset -n ne-base prometheus-node-exporter -o yaml` on the chart default, cut down to the `hostNetwork`, `hostPID` and `volumes` lines under `spec.template.spec`; `...` marks cut lines.\n")
    lines, keep = pod_spec_lines(text("daemonset-base.yaml", "s3"))
    if lines is None or not keep:
        invalid.append("s3: no hostNetwork, hostPID or volumes line in daemonset-base.yaml")
        out.append(block("(missing)"))
    else:
        out.append(block(excerpt(lines, keep)))
    late.append("### s3: lines absent from each DaemonSet\n")
    rows = []
    for c in S3_SUPPORT:
        lines, keep = pod_spec_lines(text(f"daemonset-{c}.yaml", "s3"))
        if lines is None:
            invalid.append(f"s3: no spec.template.spec in daemonset-{c}.yaml")
            continue
        kept = [lines[i].strip() for i in keep]
        absent = [f for f in FIELDS if f not in kept]
        rows.append(f"{c}: absent " + (", ".join(f"`{f}`" for f in absent) if absent else "none"))
    late.append("\n".join(f"- {r}" for r in rows) + "\n")

if "s4" in sections:
    counter = text("countermeasure.txt", "s4")
    d = dry_run(counter, "monitoring")
    if d is None:
        invalid.append("s4: no dry-run output of the monitoring namespace in countermeasure.txt")
    out.append(f"## s4: {TITLES['s4']} (job `s4`)\n")
    out.append("kube-prometheus-stack with `prometheus-node-exporter.namespaceOverride=monitoring-ne`, where `monitoring-ne` was created first with `pod-security.kubernetes.io/enforce=privileged`.\n")
    out.append("`kubectl label --dry-run=server --overwrite ns monitoring pod-security.kubernetes.io/enforce=baseline`\n")
    out.append(block(d or "(missing)"))
    where = []
    for m in re.finditer(r"^\$ kubectl get pods -n (\S+) -o wide\n(.*?)^\[exit", counter, re.M | re.S):
        for l in m.group(2).splitlines():
            if l.split() and "node-exporter" in l.split()[0]:
                where.append(f"{l.split()[0]} in namespace {m.group(1)}")
    if not where:
        invalid.append("s4: no node-exporter Pod in the Pod lists of countermeasure.txt")
    late.append("### s4: where the node-exporter Pod ran\n")
    late.append(block("\n".join(where) or "(missing)"))

env = []
for s in sections if merged else ["all"]:
    for l in text("versions.txt", s).splitlines():
        if "=" in l and l.split("=", 1) not in env:
            env.append(l.split("=", 1))
late.append("### Versions\n")
late.append("| | |\n|---|---|")
late += [f"| {k} | {v} |" for k, v in env]

for x in dict.fromkeys(invalid):
    print(f"RUN INVALID: {x}")
if invalid:
    print()
print("\n".join(out))
print("## Numbers not used\n")
print("\n".join(late))
