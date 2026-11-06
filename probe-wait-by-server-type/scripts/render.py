import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conditions import stages, work_ms
from servers import SERVERS

cluster = os.environ["CLUSTER"]
what, server, mode = sys.argv[1], sys.argv[2], sys.argv[3]
s = SERVERS[server]
probe_port = s.get("probe_port", 8080)


def probe(path):
    return {"httpGet": {"path": path, "port": probe_port}, "timeoutSeconds": 1,
            "periodSeconds": 10, "failureThreshold": 3, "successThreshold": 1}


def app():
    env = [{"name": k, "value": v} for k, v in s["env"].items()] + [{"name": "WORK_MS", "value": work_ms(server)}]
    container = {
        "name": "app", "image": s["image"], "imagePullPolicy": "Never",
        "command": s["command"], "env": env,
        "ports": [{"containerPort": p} for p in sorted({8080, probe_port})],
        "resources": {"requests": {"cpu": "100m", "memory": "64Mi"}},
        "livenessProbe": probe(s["probes"][0]),
        "readinessProbe": probe(s["probes"][1]),
    }
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
    if mode == "capacity":
        env += [{"name": "MODE", "value": "closed"}, {"name": "VUS", "value": "60"},
                {"name": "DURATION", "value": "40s"}]
    else:
        st = stages(mode)
        env += [{"name": "MODE", "value": "open"},
                {"name": "RATES", "value": ",".join(str(r) for r, _, _ in st)},
                {"name": "DURS", "value": ",".join(str(d) for _, d, _ in st)}]
    return {
        "apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "k6"},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 300, "template": {"spec": {
            "restartPolicy": "Never",
            "nodeSelector": {"kubernetes.io/hostname": cluster + "-control-plane"},
            "securityContext": {"sysctls": [{"name": "net.ipv4.ip_local_port_range", "value": "1024 65535"},
                                            {"name": "net.ipv4.tcp_tw_reuse", "value": "1"}]},
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


def prober(targets):
    return {
        "apiVersion": "v1", "kind": "Pod", "metadata": {"name": "prober", "labels": {"app": "prober"}},
        "spec": {
            "restartPolicy": "Never",
            "nodeSelector": {"kubernetes.io/hostname": cluster + "-control-plane"},
            "containers": [{
                "name": "prober", "image": os.environ["PROBER_TAG"], "imagePullPolicy": "Never",
                "command": ["python", "-u", "/scripts/prober.py", s["probes"][0], str(probe_port)] + targets,
                "volumeMounts": [{"name": "script", "mountPath": "/scripts"}],
            }],
            "volumes": [{"name": "script", "configMap": {"name": "prober-script"}}],
        },
    }


if what == "app":
    print(json.dumps(app(), indent=1))
elif what == "k6":
    print(json.dumps(k6(sys.argv[4]), indent=1))
elif what == "prober":
    print(json.dumps(prober(sys.argv[4:]), indent=1))
elif what == "stages":
    for r, d, f in stages(mode):
        print(r, d, f)
elif what == "image":
    print(s["image"], s["build"])
