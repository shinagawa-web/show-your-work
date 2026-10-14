import pathlib
import sys

import checks

results = pathlib.Path(sys.argv[1])
names = sys.argv[2:]
rows = checks.evaluate(results, names)
invalid = [f"{n}: {m}" for n, missing, _, _ in rows for m in missing]
for x in invalid:
    print(f"RUN INVALID: {x}")
if invalid:
    print()
print("## Predictions\n")
print("| | data | holds | observed |")
print("|---|---|---|---|")
for name, missing, holds, detail in rows:
    print(f"| {name} | {'ok' if not missing else 'missing'} | {'-' if missing else 'yes' if holds else 'no'} | {detail.replace('|', '/')} |")
print()
files = [("00-environment.txt", "Environment"), ("01-timing.txt", "Operation timing"), ("elapsed.txt", "Elapsed")]
files += [(f"{n}.txt", f"{n}: operation, spec.replicas and managedFields") for n in names]
files += [(f"screen-{n}.txt", f"{n}: kubectl output") for n in ("c1-1", "c3", "c4", "c7")]
files += [("sync.log", "HPA syncs (kube-controller-manager log)"), ("hpa.log", "HPA status changes"), ("pods.log", "Pod phase changes"), ("metrics.log", "First appearance in the metrics API"), ("events.txt", "Events")]
for f, title in files:
    p = results / f
    print(f"## {title}\n")
    print(f"`{f}`\n")
    print("```")
    print(p.read_text().rstrip() if p.exists() else "(missing)")
    print("```\n")
