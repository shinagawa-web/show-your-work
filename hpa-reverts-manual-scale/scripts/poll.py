import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone

out = pathlib.Path(sys.argv[1])
stop = out / ".stop"


def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:12]


def get(args):
    p = subprocess.run(["kubectl", *args], capture_output=True, text=True)
    return json.loads(p.stdout) if p.returncode == 0 and p.stdout else None


def cond(h, t):
    c = next((c for c in h.get("status", {}).get("conditions", []) if c["type"] == t), None)
    return f"{c['status']}/{c['reason']}" if c else "-"


files = {n: open(out / f"{n}.log", "a", buffering=1) for n in ("replicas", "hpa", "pods", "metrics")}
seen_hpa, seen_pod, seen_metric = {}, {}, set()
while not stop.exists():
    t0 = time.time()
    objs = get(["get", "deploy,hpa,pods", "-o", "json"])
    now = ts()
    if objs:
        for o in objs["items"]:
            name = o["metadata"]["name"]
            if o["kind"] == "Deployment":
                s = o.get("status", {})
                files["replicas"].write(f"{now} {name} spec={o['spec']['replicas']} status={s.get('replicas', 0)} ready={s.get('readyReplicas', 0)}\n")
            elif o["kind"] == "HorizontalPodAutoscaler":
                s = o.get("status", {})
                util = next((m["resource"]["current"].get("averageUtilization") for m in s.get("currentMetrics") or [] if m.get("type") == "Resource"), None)
                line = f"desired={s.get('desiredReplicas')} current={s.get('currentReplicas')} util={util} lastScaleTime={s.get('lastScaleTime')} AbleToScale={cond(o, 'AbleToScale')} ScalingActive={cond(o, 'ScalingActive')} ScalingLimited={cond(o, 'ScalingLimited')}"
                if seen_hpa.get(name) != line:
                    seen_hpa[name] = line
                    files["hpa"].write(f"{now} {name} {line}\n")
            elif o["kind"] == "Pod":
                ready = next((c["status"] for c in o.get("status", {}).get("conditions", []) if c["type"] == "Ready"), "-")
                line = f"phase={o.get('status', {}).get('phase')} ready={ready} deleting={'deletionTimestamp' in o['metadata']}"
                if seen_pod.get(name) != line:
                    seen_pod[name] = line
                    files["pods"].write(f"{now} {name} {line}\n")
    m = get(["get", "--raw", "/apis/metrics.k8s.io/v1beta1/namespaces/default/pods"])
    now = ts()
    if m:
        for i in m["items"]:
            name = i["metadata"]["name"]
            if name not in seen_metric:
                seen_metric.add(name)
                files["metrics"].write(f"{now} {name} timestamp={i['timestamp']} window={i['window']}\n")
    time.sleep(max(0, 1 - (time.time() - t0)))
