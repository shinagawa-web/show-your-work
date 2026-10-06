#!/usr/bin/env python3
"""Summarize one run directory, or (given the results root) write summary.md
with one row per run. Values are copied from the raw files without judgement."""
import json, os, re, sys


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def lines(path):
    try:
        with open(path) as f:
            return f.read().splitlines()
    except OSError:
        return []


def run_summary(d):
    s = {}
    events = [json.loads(l) for l in lines(os.path.join(d, "events.jsonl")) if l.strip()]
    seq = []
    for e in events:
        if e.get("Type") != "container":
            continue
        a = e.get("Actor", {}).get("Attributes", {})
        item = e["Action"]
        if "signal" in a:
            item += f"(signal={a['signal']})"
        if "exitCode" in a:
            item += f"(exitCode={a['exitCode']})"
        seq.append(item)
    s["events"] = ", ".join(seq)
    t = {}
    for e in events:
        t.setdefault(e["Action"], e["timeNano"])
    if "oom" in t and "die" in t:
        s["oom_vs_die"] = f"{'oom<die' if t['oom'] < t['die'] else 'die<oom'} ({(t['die'] - t['oom']) / 1e6:+.1f} ms die-oom)"
    else:
        s["oom_vs_die"] = "-"
    k = lines(os.path.join(d, "kernel.txt"))
    head = [l.split("kernel: ", 1)[1] for l in k if re.search(r"(Memory cgroup out of memory|Out of memory): Killed process", l)]
    cons = [l.split("kernel: ", 1)[1] for l in k if "oom-kill:constraint=" in l]
    s["oom_record"] = " | ".join(head) if head else "none"
    killed = [re.search(r"Killed process (\d+) \(([^)]*)\)", h) for h in head]
    s["killed"] = ", ".join(f"{m.group(1)} ({m.group(2)})" for m in killed if m) or "-"
    def field(c, key):
        m = re.search(key + r"=([^,]*)", c)
        return m.group(1) if m else None
    cl = []
    for c in cons:
        parts = [f"constraint={field(c, 'constraint')}"]
        if field(c, "oom_memcg") is not None:
            parts.append(f"oom_memcg={field(c, 'oom_memcg')}")
        if "global_oom" in c:
            parts.append("global_oom")
        parts.append(f"task_memcg={field(c, 'task_memcg')}")
        parts.append(f"task={field(c, 'task')} pid={field(c, 'pid')}")
        cl.append(" ".join(parts))
    s["constraint"] = " | ".join(cl) if cl else "none"
    st = (load(os.path.join(d, "inspect-start.json"), [{}]) or [{}])[0]
    en = (load(os.path.join(d, "inspect-end.json"), [{}]) or [{}])[0]
    cid = en.get("Id", st.get("Id", ""))
    s["container_scope"] = f"/system.slice/docker-{cid}.scope" if cid else "-"
    s["State.Pid"] = st.get("State", {}).get("Pid")
    s["HostConfig.Init"] = json.dumps(st.get("HostConfig", {}).get("Init"))
    s["children"] = (lines(os.path.join(d, "start.txt")) or [""])[0].split("children=")[-1]
    s["killed_pid_eq_State.Pid"] = "-"
    if killed and killed[0]:
        s["killed_pid_eq_State.Pid"] = str(int(killed[0].group(1)) == s["State.Pid"]).lower()
    memcg_eq = []
    for c in cons:
        for key in ("oom_memcg", "task_memcg"):
            v = field(c, key)
            if v is not None:
                memcg_eq.append(f"{key}{'==' if v == s['container_scope'] else '!='}container")
    s["memcg_vs_container"] = ", ".join(memcg_eq) or "-"
    stt = en.get("State", {})
    s["ExitCode"] = stt.get("ExitCode")
    s["OOMKilled"] = json.dumps(stt.get("OOMKilled"))
    s["RestartCount"] = en.get("RestartCount")
    s["Running"] = json.dumps(stt.get("Running"))
    r = [l.split(": ", 1)[-1] for l in lines(os.path.join(d, "dockerd.txt")) if "restarting container" in l]
    s["restarting_container_log"] = " | ".join(r) if r else "none"
    for extra in ("action.txt", "sizing.txt", "notes.txt"):
        x = lines(os.path.join(d, extra))
        if x:
            s[extra[:-4]] = " / ".join(x)
    return s


def main():
    root = sys.argv[1]
    if os.path.exists(os.path.join(root, "events.jsonl")):
        for k, v in run_summary(root).items():
            print(f"{k}: {v}")
        return
    cols = ["cond", "run", "ExitCode", "OOMKilled", "RestartCount", "Running", "events", "oom_vs_die",
            "oom_record", "constraint", "memcg_vs_container", "HostConfig.Init", "State.Pid", "children",
            "killed", "killed_pid_eq_State.Pid", "restarting_container_log"]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    extras = []
    conds = sorted((c for c in os.listdir(root) if re.fullmatch(r"C\d+", c)), key=lambda c: int(c[1:]))
    for c in conds:
        for r in sorted(os.listdir(os.path.join(root, c)), key=int):
            s = run_summary(os.path.join(root, c, r))
            s.update(cond=c, run=r)
            out.append("| " + " | ".join(str(s.get(k, "")).replace("|", "\\|") for k in cols) + " |")
            for extra in ("action", "sizing", "notes"):
                if extra in s:
                    extras.append(f"- {c} run {r} {extra}: {s[extra]}")
    with open(os.path.join(root, "summary.md"), "w") as f:
        f.write("# Summary\n\n" + "\n".join(out) + "\n\n" + "\n".join(extras) + "\n")


main()
