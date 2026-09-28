import json
import pathlib
import re
import sys

results = pathlib.Path(sys.argv[1])
order = "default legacy general baseline restricted netadmin sysadmin app-uid0-legacy app-uid0-default app-uid0-gid0-legacy app-uid0-gid0-default".split()
rows = {}
for f in [*results.glob("profiles/*.txt"), *results.glob("uid0/*.txt")]:
    text = f.read_text()
    state = re.search(r"^state: (.*)$", text, re.M)
    row = {"state": state.group(1) if state else ""}
    for m in re.finditer(r"^=== (\S+)\n(.*?)^\[exit (\d+)\]", text, re.M | re.S):
        row[m.group(1)] = m.group(3)
    cap = re.search(r"^CapEff:\s+(\S+)", text, re.M)
    row["CapEff"] = cap.group(1) if cap else "-"
    rows[f.stem] = row

keys = []
for r in rows.values():
    for k in r:
        if k not in keys and k not in ("state", "CapEff", "target_pid"):
            keys.append(k)

def state(s):
    try:
        d = json.loads(s)
    except ValueError:
        return s or "-"
    k = next(iter(d), "-")
    return k + ("/" + d[k].get("reason", "") if d[k].get("reason") else "")

names = sorted(rows, key=lambda p: order.index(p) if p in order else 99)
print("# Profile x probe exit codes\n")
print("0 means the command exited 0. Raw output is in profiles/<profile>.txt and uid0/<pod>-<profile>.txt.\n")
print("| probe | " + " | ".join(names) + " |")
print("|---|" + "---|" * len(names))
print("| state | " + " | ".join(state(rows[n]["state"]) for n in names) + " |")
print("| CapEff | " + " | ".join(rows[n]["CapEff"] for n in names) + " |")
for k in keys:
    print(f"| {k} | " + " | ".join(rows[n].get(k, "-") for n in names) + " |")
