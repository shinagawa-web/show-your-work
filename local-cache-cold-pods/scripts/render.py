import json
import os
import sys

e = os.environ
what = sys.argv[1]


def lst(items):
    return {"apiVersion": "v1", "kind": "List", "items": items}


def pg(ip):
    return lst([
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "pg"},
         "spec": {"ports": [{"name": "pg", "port": 5432, "targetPort": 5432}]}},
        {"apiVersion": "v1", "kind": "Endpoints", "metadata": {"name": "pg"},
         "subsets": [{"addresses": [{"ip": ip}], "ports": [{"name": "pg", "port": 5432}]}]},
    ])


def app(strategy):
    labels = {"app": "app"}
    env = {
        "DATABASE_URL": "postgres://postgres:pg@pg:5432/postgres?sslmode=disable",
        "POOL_MAX": e["POOL_MAX"],
        "CACHE_SIZE": e["CACHE_SIZE"],
        "CACHE_TTL_SECONDS": e["CACHE_TTL_SECONDS"],
        "STATS_DIR": "/stats",
    }
    envs = [{"name": k, "value": v} for k, v in env.items()]
    envs.append({"name": "POD_NAME", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}})
    spec = {"replicas": 4, "selector": {"matchLabels": labels},
            "template": {"metadata": {"labels": labels}, "spec": {"containers": [{
                "name": "app", "image": e["APP_IMAGE"], "imagePullPolicy": "Never",
                "env": envs,
                "ports": [{"containerPort": 8080}],
                "readinessProbe": {"httpGet": {"path": "/ready", "port": 8080}, "periodSeconds": 1},
                "volumeMounts": [{"name": "stats", "mountPath": "/stats"}]}],
                "volumes": [{"name": "stats", "hostPath": {"path": "/stats", "type": "Directory"}}]}}}
    if strategy.startswith("surge1"):
        spec["strategy"] = {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
    if strategy in ("surge1", "default-ready"):
        spec["minReadySeconds"] = int(e["MIN_READY"])
    return lst([
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "app"}, "spec": spec},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "app"},
         "spec": {"selector": labels, "ports": [{"port": 80, "targetPort": 8080}]}},
    ])


def k6(seconds):
    env = {"BASE": "http://app", "RATE": e["RATE"], "SECONDS": str(seconds),
           "KEYS": e["KEYS"], "ZIPF_S": e["ZIPF_S"], "GOGC": e["K6_GOGC"],
           "START_RATE": e["START_RATE"], "RAMP": e["RAMP"]}
    return {
        "apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "k6"},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": seconds + 60, "template": {"spec": {
            "restartPolicy": "Never",
            "securityContext": {"sysctls": [{"name": "net.ipv4.ip_local_port_range", "value": "1024 65535"}]},
            "containers": [{
                "name": "k6", "image": e["K6_TAG"], "imagePullPolicy": "Never",
                "args": ["run", "--quiet", "--no-usage-report", "--log-format=raw",
                         "--log-output=file=/out/k6.log", "/scripts/load.js"],
                "env": [{"name": k, "value": v} for k, v in env.items()],
                "volumeMounts": [{"name": "script", "mountPath": "/scripts"}, {"name": "out", "mountPath": "/out"}],
            }],
            "volumes": [{"name": "script", "configMap": {"name": "k6-script"}},
                        {"name": "out", "hostPath": {"path": "/k6out", "type": "Directory"}}],
        }}},
    }


if what == "pg":
    out = pg(sys.argv[2])
elif what == "app":
    out = app(sys.argv[2])
elif what == "k6":
    out = k6(int(sys.argv[2]))
print(json.dumps(out, indent=1))
