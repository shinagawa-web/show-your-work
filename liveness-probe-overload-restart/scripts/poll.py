import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

results = sys.argv[1]
node = sys.argv[2]
API = "http://127.0.0.1:8001"
stop = threading.Event()
os.makedirs(os.path.join(results, "previous"), exist_ok=True)


def get(path, timeout=5):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return r.read()


def status_loop():
    seen = {}
    with open(os.path.join(results, "pods.jsonl"), "w") as pf, open(os.path.join(results, "eps.jsonl"), "w") as ef:
        nxt = time.time()
        while not stop.is_set():
            t = time.time()
            try:
                pods = json.loads(get("/api/v1/namespaces/default/pods?labelSelector=app%3Dapp"))
                rows = []
                for p in pods["items"]:
                    cs = (p["status"].get("containerStatuses") or [{}])[0]
                    rows.append({"pod": p["metadata"]["name"], "node": p["spec"].get("nodeName"),
                                 "phase": p["status"].get("phase"),
                                 "ready": cs.get("ready"), "restarts": cs.get("restartCount", 0),
                                 "state": cs.get("state"), "lastState": cs.get("lastState"),
                                 "containerID": cs.get("containerID")})
                    name, rc = rows[-1]["pod"], rows[-1]["restarts"]
                    if name in seen and rc > seen[name]:
                        threading.Thread(target=previous, args=(name, rc, rows[-1]["lastState"])).start()
                    seen[name] = rc
                pf.write(json.dumps({"t": t, "pods": rows}) + "\n")
                es = json.loads(get("/apis/discovery.k8s.io/v1/namespaces/default/endpointslices"
                                    "?labelSelector=kubernetes.io%2Fservice-name%3Dapp"))
                eps = []
                for s in es["items"]:
                    for e in s.get("endpoints") or []:
                        eps.append({"pod": (e.get("targetRef") or {}).get("name"), "ready": e["conditions"].get("ready"),
                                    "serving": e["conditions"].get("serving"),
                                    "terminating": e["conditions"].get("terminating")})
                ef.write(json.dumps({"t": t, "eps": eps}) + "\n")
            except Exception as e:
                pf.write(json.dumps({"t": t, "error": str(e)}) + "\n")
            pf.flush()
            ef.flush()
            nxt += 1
            stop.wait(max(0, nxt - time.time()))


def previous(pod, rc, last):
    out = os.path.join(results, "previous", "%s-%d.log" % (pod, rc))
    r = subprocess.run(["kubectl", "logs", pod, "-c", "app", "--previous"], capture_output=True, text=True)
    with open(out, "w") as f:
        f.write(json.dumps({"detected": time.time(), "restartCount": rc, "lastState": last}) + "\n")
        f.write(r.stdout)
        f.write(r.stderr)


def probes_loop():
    with open(os.path.join(results, "probes.txt"), "w") as f:
        nxt = time.time()
        while not stop.is_set():
            t = time.time()
            try:
                body = get("/api/v1/nodes/%s/proxy/metrics/probes" % node).decode()
                f.write("# t=%.3f\n" % t)
                for line in body.splitlines():
                    if line.startswith("prober_probe_total") and 'namespace="default"' in line:
                        f.write(line + "\n")
            except Exception as e:
                f.write("# t=%.3f error %s\n" % (t, e))
            f.flush()
            nxt += 1
            stop.wait(max(0, nxt - time.time()))


def events_loop():
    log = {"reconnects": 0, "errors": []}
    with open(os.path.join(results, "events.jsonl"), "w") as f:
        lst = json.loads(get("/api/v1/namespaces/default/events"))
        rv = lst["metadata"]["resourceVersion"]
        for o in lst["items"]:
            f.write(json.dumps({"recv": time.time(), "type": "LISTED", "object": o}) + "\n")
        log["start_rv"] = rv
        while not stop.is_set():
            try:
                with urllib.request.urlopen(API + "/api/v1/namespaces/default/events?watch=1&allowWatchBookmarks=true"
                                            "&timeoutSeconds=5&resourceVersion=" + rv, timeout=30) as r:
                    while True:
                        line = r.readline()
                        if not line:
                            break
                        ev = json.loads(line)
                        if ev["type"] == "ERROR":
                            log["errors"].append(ev["object"])
                            break
                        rv = ev["object"]["metadata"]["resourceVersion"]
                        if ev["type"] == "BOOKMARK":
                            continue
                        f.write(json.dumps({"recv": time.time(), "type": ev["type"], "object": ev["object"]}) + "\n")
                        f.flush()
            except Exception as e:
                log["errors"].append(str(e))
                time.sleep(0.2)
            log["reconnects"] += 1
    log["end_rv"] = rv
    with open(os.path.join(results, "events-watch.json"), "w") as f:
        json.dump(log, f)


threads = [threading.Thread(target=x) for x in (status_loop, probes_loop, events_loop)]
for th in threads:
    th.start()
stopfile = os.path.join(results, ".stop")
while not os.path.exists(stopfile):
    time.sleep(0.5)
stop.set()
for th in threads:
    th.join()
