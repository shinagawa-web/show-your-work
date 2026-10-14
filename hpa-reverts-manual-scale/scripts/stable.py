import json
import subprocess
import sys
from datetime import datetime, timezone

NEED = int(sys.argv[1])
names = sys.argv[2:]
kcm = "kube-controller-manager-hpa-control-plane"


def kubectl(*args):
    p = subprocess.run(["kubectl", *args], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


def parse(t):
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def seconds(d):
    m, _, s = d.rstrip("s").rpartition("m")
    return float(m or 0) * 60 + float(s or 0)


log = kubectl("-n", "kube-system", "logs", kcm, f"--since={15 * (NEED + 1) + 5}s")
syncs = subprocess.run([sys.executable, "-B", sys.argv[0].replace("stable.py", "syncs.py"), *names], input=log, capture_output=True, text=True).stdout
results = {n: [] for n in names}
for ln in syncs.splitlines():
    p = ln.split(" ", 3)
    if len(p) >= 3 and p[2] in ("decided", "rescaled", "error"):
        results[p[1]].append((p[0], p[2]))
pods = json.loads(kubectl("get", "pods", "-o", "json") or '{"items": []}')["items"]
metrics = {i["metadata"]["name"]: i for i in json.loads(kubectl("get", "--raw", "/apis/metrics.k8s.io/v1beta1/namespaces/default/pods") or '{"items": []}')["items"]}
bad = []
for n in names:
    last = results[n][-NEED:]
    if len(last) < NEED or any(k != "decided" for _, k in last):
        bad.append(f"{n}: last syncs {last}")
    for pod in pods:
        if pod["metadata"].get("labels", {}).get("app") != n or "deletionTimestamp" in pod["metadata"]:
            continue
        name = pod["metadata"]["name"]
        ready = next((c for c in pod.get("status", {}).get("conditions", []) if c["type"] == "Ready"), None)
        m = metrics.get(name)
        if not ready or ready["status"] != "True":
            bad.append(f"{name}: not ready")
        elif not m:
            bad.append(f"{name}: not in the metrics API")
        else:
            start = parse(m["timestamp"]).timestamp() - seconds(m["window"])
            if start < parse(ready["lastTransitionTime"]).timestamp():
                bad.append(f"{name}: metric window starts before Ready")
now = datetime.now(timezone.utc).strftime("%H:%M:%S")
print(f"{now} " + ("; ".join(bad) if bad else "stable"))
sys.exit(1 if bad else 0)
