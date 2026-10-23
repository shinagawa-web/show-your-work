import pathlib
import re
import sys

results = pathlib.Path(sys.argv[1])
conds = sys.argv[2].split()
invalid = list(sys.argv[3:])


def text(name):
    f = results / name
    if not f.exists():
        invalid.append(f"{name} is missing")
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
    return (m.group(1).rstrip(), m.group(2)) if m else None


cells_rows = table(text("cells.txt"), "| row |")
labels_rows = table(text("labels.txt"), "| condition | pod IP |")
alert_rows = table(text("alert.txt"), "| condition | expression |")
dryrun = text("dryrun.txt")
counter = text("countermeasure.txt")
elapsed = text("elapsed.txt").strip()

if not cells_rows:
    invalid.append("no verdict table in cells.txt")
for c in conds:
    if c not in labels_rows:
        invalid.append(f"no instance of variant {c} in labels.txt")
    if c not in alert_rows:
        invalid.append(f"no alert expression result of variant {c} in alert.txt")
    if dry_run(dryrun, f"ne-{c}") is None:
        invalid.append(f"no dry-run output of variant {c} in dryrun.txt")
if dry_run(counter, "monitoring") is None:
    invalid.append("no dry-run output of the monitoring namespace in countermeasure.txt")

for x in invalid:
    print(f"RUN INVALID: {x}")
if invalid:
    print()

print("## Verdicts\n")
print("Each cell says whose value the series group reports in that variant, judged by `scripts/cells.py` against the node and the Pod read before and after the scrape. Details are in `cells.txt` and `labels.txt`.\n")
print("| series group | " + " | ".join(conds) + " |")
print("|---|" + "---|" * len(conds))
for row, vals in cells_rows.items():
    print(f"| {row} | " + " | ".join(vals) + " |")
print("| instance | " + " | ".join(labels_rows.get(c, ["-"] * 4)[2] for c in conds) + " |")
print()

print("## Alert expression\n")
print("`(node_nf_conntrack_entries{job=\"node-exporter\"} / node_nf_conntrack_entries_limit) > 0.75`, evaluated while the node's conntrack table is filled above the threshold (`alert.txt`, `conntrack.txt`).\n")
print("| variant | expression |")
print("|---|---|")
for c in conds:
    print(f"| {c} | {alert_rows.get(c, ['-'])[0]} |")
print()

print("## Baseline dry-run per variant\n")
print("`kubectl label --dry-run=server --overwrite ns ne-<variant> pod-security.kubernetes.io/enforce=baseline` (`dryrun.txt`).\n")
for c in conds:
    d = dry_run(dryrun, f"ne-{c}")
    print(f"### {c}\n")
    print("```")
    print(d[0] if d else "(missing)")
    print("```\n")

print("## Countermeasure\n")
print("kube-prometheus-stack with `prometheus-node-exporter.namespaceOverride` set to a namespace labelled `enforce=privileged`; Baseline dry-run of the Prometheus namespace (`countermeasure.txt`).\n")
d = dry_run(counter, "monitoring")
print("```")
print(d[0] if d else "(missing)")
print("```\n")

print("## Elapsed\n")
print(f"`{elapsed}`")
