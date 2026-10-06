#!/usr/bin/env python3
"""Per run of race.sh: (1) whether OOM reached Docker by three records
(docker events oom, OOMKilled, ctr /tasks/oom), (2) run conditions, (3) for
traced runs, event times in ms from mark_victim on the ftrace boot clock
(user-space times converted with the CLOCK_REALTIME/CLOCK_BOOTTIME pairs).
  race-analyze.py <results>"""
import json, os, re, statistics, sys
from datetime import datetime, timezone

root = sys.argv[1]
pairs = [tuple(map(int, l.split()[1:3])) for l in open(os.path.join(root, "clock.txt"))] if os.path.exists(os.path.join(root, "clock.txt")) else []
for c in ("C1", "C2"):
    for r in os.listdir(os.path.join(root, c)) if os.path.isdir(os.path.join(root, c)) else []:
        for l in open(os.path.join(root, c, r, "pre.txt")):
            if l.startswith("clock "):
                pairs.append(tuple(map(int, l.split()[1:3])))
offs = [a - b for a, b in pairs]
OFF = int(statistics.median(offs))
print(f"realtime-boottime offset: median {OFF} ns, spread {max(offs) - min(offs)} ns over {len(offs)} pairs\n")

ft = []
rx = re.compile(r"^\s*(.+?)-(\d+)\s+\(\s*(\d+)\)\s+\[\d+\]\s+\S+\s+([\d.]+):\s+(\S+):\s*(.*)$")
if os.path.exists(os.path.join(root, "ftrace.txt")):
    for l in open(os.path.join(root, "ftrace.txt")):
        m = rx.match(l)
        if m:
            ft.append(dict(comm=m.group(1), tid=int(m.group(2)), tgid=int(m.group(3)),
                           ts=int(round(float(m.group(4)) * 1e9)), ev=m.group(5), f=m.group(6)))

ctr = []
for l in (open(os.path.join(root, "ctr-events.txt")) if os.path.exists(os.path.join(root, "ctr-events.txt")) else []):
    m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.(\d+) \+0000 UTC moby (\S+) (.*)", l)
    if m:
        sec = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        ns = int(sec) * 10**9 + int(m.group(2).ljust(9, "0")[:9])
        ctr.append((ns, m.group(3), m.group(4)))

rows, judge, cond_rows = [], [], []
for c in ("C1", "C2"):
    d0 = os.path.join(root, c)
    if not os.path.isdir(d0):
        continue
    for r in sorted(os.listdir(d0), key=int):
        d = os.path.join(d0, r)
        pre = dict(l.strip().split("=", 1) for l in open(os.path.join(d, "pre.txt")) if "=" in l)
        mem = [l for l in open(os.path.join(d, "pre.txt")) if l.startswith("MemAvailable")][0].split()[1]
        st = json.load(open(os.path.join(d, "inspect-start.json")))[0]
        en = json.load(open(os.path.join(d, "inspect-end.json")))[0]
        cid, pid1 = en["Id"], st["State"]["Pid"]
        ev = [json.loads(l) for l in open(os.path.join(d, "events.jsonl"))]
        d_oom = [e["timeNano"] for e in ev if e["Action"] == "oom"]
        d_die = [e["timeNano"] for e in ev if e["Action"] == "die"]
        c_oom = [ns for ns, topic, body in ctr if topic == "/tasks/oom" and cid in body]
        c_exit = [ns for ns, topic, body in ctr if topic == "/tasks/exit" and cid in body]
        judge.append((c, r, pre["trace"], bool(d_oom), en["State"]["OOMKilled"], bool(c_oom) if ctr else "n/a"))
        k = open(os.path.join(d, "kernel.txt")).read()
        killed = re.findall(r"Killed process (\d+) \(([^)]*)\)", k)
        cons = re.findall(r"constraint=(\w+)", k)
        bound = ["oom_memcg=" + m.split("/")[-1][:19] + "..." for m in re.findall(r"oom_memcg=([^,]+)", k)] + (["global_oom"] if "global_oom" in k else [])
        alloc = re.search(r"holding (\d+) MiB|allocated (\d+) MiB", open(os.path.join(d, "container.log")).read())
        sizing = open(os.path.join(d, "sizing.txt")).read().split("\n")[0] if os.path.exists(os.path.join(d, "sizing.txt")) else ""
        last_alloc = re.findall(r"(?:allocated|holding) (\d+) MiB", open(os.path.join(d, "container.log")).read())
        cond_rows.append((c, r, pre["trace"], pre["containers"], pre["docker_scopes"], mem + "kB",
                          ",".join(f"{p}({n}){'=PID1' if int(p) == pid1 else ''}" for p, n in killed) or "-",
                          ",".join(cons) or "-", ",".join(bound) or "-", (last_alloc[-1] + "MiB last logged") if last_alloc else "-", sizing))
        if pre["trace"] != "on":
            continue
        mv = [e for e in ft if e["ev"] == "mark_victim" and f"pid={pid1} " in e["f"]]
        if not mv:
            rows.append((c, r, bool(d_oom), "no mark_victim for PID 1"))
            continue
        t0 = mv[0]["ts"]
        ms = lambda ts: f"{(ts - t0) / 1e6:+.1f}" if ts is not None else "-"
        first = lambda pred: next((e["ts"] for e in ft if e["ts"] >= t0 - 10**7 and pred(e)), None)
        scope = f"docker-{cid}.scope"
        exit_ = first(lambda e: e["ev"] == "sched_process_exit" and f"pid={pid1} " in e["f"])
        empty = first(lambda e: e["ev"] == "cgroup_notify_populated" and scope in e["f"] and e["f"].endswith("val=0"))
        rmdir = first(lambda e: e["ev"] == "cgroup_rmdir" and scope in e["f"])
        works = [e["ts"] for e in ft if e["ev"] == "kn_workfn" and t0 <= e["ts"] <= (rmdir or t0) + 5 * 10**7]
        opens = {}
        for i, e in enumerate(ft):
            if e["ev"] == "open" and e["ts"] >= t0 and scope in e["f"]:
                fname = "memory.events" if "memory.events" in e["f"] else "cgroup.events" if "cgroup.events" in e["f"] else None
                if fname and fname not in opens:
                    ret = next((x for x in ft[i + 1:] if x["ev"] == "open_ret" and x["tid"] == e["tid"]), None)
                    opens[fname] = (e["ts"], re.search(r"ret=(-?\d+)", ret["f"]).group(1) if ret else "?")
        b = lambda ns: ns - OFF if ns is not None else None
        rows.append((c, r, bool(d_oom),
                     "exit " + ms(exit_), "empty " + ms(empty),
                     "workfn " + ",".join(ms(w) for w in works[:3]),
                     "open cgroup.events " + (ms(opens["cgroup.events"][0]) + " ret=" + opens["cgroup.events"][1] if "cgroup.events" in opens else "-"),
                     "open memory.events " + (ms(opens["memory.events"][0]) + " ret=" + opens["memory.events"][1] if "memory.events" in opens else "-"),
                     "rmdir " + ms(rmdir),
                     "ctr exit " + ms(b(c_exit[0]) if c_exit else None), "ctr oom " + ms(b(c_oom[0]) if c_oom else None),
                     "docker die " + ms(b(d_die[0]) if d_die else None), "docker oom " + ms(b(d_oom[0]) if d_oom else None)))

print("## 1 judgement: cond run trace | docker_events_oom OOMKilled ctr_tasks_oom | agree")
for j in judge:
    print(" ".join(map(str, j[:3])), "|", j[3], j[4], j[5], "|", "agree" if j[3] == j[4] and j[5] in (j[3], "n/a") else "DISAGREE")
print("\n## 2 conditions: cond run trace | containers_before docker_scopes_before MemAvailable | killed constraint boundary | subject alloc | sizing")
for x in cond_rows:
    print(" | ".join(map(str, x)))
print("\n## 3 times (ms from mark_victim), traced runs: cond run docker_oom | ...")
for x in rows:
    print(" | ".join(map(str, x)))
