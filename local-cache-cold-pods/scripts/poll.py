import json
import os
import sys
import threading
import time
import urllib.request

results, pg_cgroup, node_cgroup = sys.argv[1:4]
API = "http://127.0.0.1:8001"
stop = threading.Event()


def pods_loop():
    with open(os.path.join(results, "pods.jsonl"), "w") as f:
        nxt = time.time()
        while not stop.is_set():
            t = time.time()
            try:
                with urllib.request.urlopen(API + "/api/v1/namespaces/default/pods?labelSelector=app%3Dapp", timeout=5) as r:
                    pods = json.loads(r.read())
                rows = []
                for p in pods["items"]:
                    ready = next((c for c in p["status"].get("conditions") or [] if c["type"] == "Ready"), {})
                    rows.append({"pod": p["metadata"]["name"], "created": p["metadata"]["creationTimestamp"],
                                 "deleting": p["metadata"].get("deletionTimestamp"),
                                 "phase": p["status"].get("phase"), "ready": ready.get("status") == "True",
                                 "ready_since": ready.get("lastTransitionTime")})
                f.write(json.dumps({"t": t, "pods": rows}) + "\n")
            except Exception as e:
                f.write(json.dumps({"t": t, "error": str(e)}) + "\n")
            f.flush()
            nxt += 0.5
            stop.wait(max(0, nxt - time.time()))


def usage(path):
    with open(os.path.join(path, "cpu.stat")) as f:
        for line in f:
            k, v = line.split()
            if k == "usage_usec":
                return int(v)


def cpus():
    out = {}
    with open("/proc/stat") as f:
        for line in f:
            if line.startswith("cpu") and line[3].isdigit():
                p = line.split()
                vals = [int(x) for x in p[1:]]
                out[p[0]] = [sum(vals), vals[3] + vals[4]]
    return out


def subgroups():
    out = {}
    for root, dirs, files in os.walk(node_cgroup):
        rel = os.path.relpath(root, node_cgroup)
        if rel.count(os.sep) >= 3:
            dirs[:] = []
        if rel != "." and "cpu.stat" in files:
            out[rel] = usage(root)
    return out


def cpu_loop():
    with open(os.path.join(results, "cpu.jsonl"), "w") as f:
        nxt = time.time()
        while not stop.is_set():
            t = time.time()
            try:
                f.write(json.dumps({"t": t, "pg_usec": usage(pg_cgroup), "node_usec": usage(node_cgroup),
                                    "proc_stat": cpus(), "node_cgroups": subgroups()}) + "\n")
            except Exception as e:
                f.write(json.dumps({"t": t, "error": str(e)}) + "\n")
            f.flush()
            nxt += 1
            stop.wait(max(0, nxt - time.time()))


threads = [threading.Thread(target=x) for x in (pods_loop, cpu_loop)]
for th in threads:
    th.start()
stopfile = os.path.join(results, ".stop")
while not os.path.exists(stopfile):
    time.sleep(0.5)
stop.set()
for th in threads:
    th.join()
