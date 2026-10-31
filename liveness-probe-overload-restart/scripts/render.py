import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conditions import CONDITIONS, stages

cluster = os.environ["CLUSTER"]
what, cond = sys.argv[1], sys.argv[2]
c = CONDITIONS[cond]


def probe(path, timeout, port):
    return {"httpGet": {"path": path, "port": port}, "timeoutSeconds": timeout,
            "periodSeconds": 10, "failureThreshold": 3, "successThreshold": 1}


def app():
    port = c.get("probe_port", 8080)
    cmd = ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:8080"]
    if port != 8080:
        cmd += ["--bind", "0.0.0.0:%d" % port]
    cmd += ["app:app"]
    env = [{"name": "PYTHONUNBUFFERED", "value": "1"}]
    container = {
        "name": "app", "image": os.environ["APP_IMAGE"], "imagePullPolicy": "Never",
        "command": cmd, "env": env,
        "ports": [{"containerPort": 8080}],
        "resources": {"requests": {"cpu": "100m", "memory": "64Mi"}},
        "readinessProbe": probe("/readyz", 1, port),
    }
    if c.get("liveness", True):
        container["livenessProbe"] = probe("/livez", c.get("liveness_timeout", 1), port)
    labels = {"app": "app"}
    deploy = {
        "apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "app"},
        "spec": {"replicas": 1, "selector": {"matchLabels": labels},
                 "template": {"metadata": {"labels": labels},
                              "spec": {"nodeSelector": {"kubernetes.io/hostname": cluster + "-worker"},
                                       "containers": [container]}}},
    }
    svc = {
        "apiVersion": "v1", "kind": "Service", "metadata": {"name": "app"},
        "spec": {"type": "ClusterIP", "selector": labels, "ports": [{"port": 80, "targetPort": 8080}]},
    }
    return {"apiVersion": "v1", "kind": "List", "items": [deploy, svc]}


def k6(base):
    env = [{"name": "BASE", "value": base}]
    if c["load"] == "closed":
        env += [{"name": "MODE", "value": "closed"}, {"name": "VUS", "value": str(c["vus"])},
                {"name": "DURATION", "value": "%ds" % c["seconds"]}]
    else:
        st = stages(cond)
        env += [{"name": "MODE", "value": "open"},
                {"name": "RATES", "value": ",".join(str(r) for r, _, _ in st)},
                {"name": "DURS", "value": ",".join(str(s) for _, s, _ in st)}]
    return {
        "apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "k6"},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 300, "template": {"spec": {
            "restartPolicy": "Never",
            "nodeSelector": {"kubernetes.io/hostname": cluster + "-control-plane"},
            "securityContext": {"sysctls": [{"name": "net.ipv4.ip_local_port_range", "value": "1024 65535"}]},
            "containers": [{
                "name": "k6", "image": os.environ["K6_TAG"], "imagePullPolicy": "Never",
                "args": ["run", "--quiet", "--no-usage-report", "--log-format=raw",
                         "--log-output=file=/out/k6.log", "/scripts/load.js"],
                "env": env,
                "volumeMounts": [{"name": "script", "mountPath": "/scripts"}, {"name": "out", "mountPath": "/out"}],
            }],
            "volumes": [{"name": "script", "configMap": {"name": "k6-script"}},
                        {"name": "out", "hostPath": {"path": "/k6out", "type": "Directory"}}],
        }}},
    }


if what == "app":
    print(json.dumps(app(), indent=1))
elif what == "k6":
    print(json.dumps(k6(sys.argv[3]), indent=1))
elif what == "stages":
    for r, s, f in stages(cond):
        print(r, s, f)
