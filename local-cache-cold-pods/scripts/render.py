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


def app():
    labels = {"app": "app"}
    env = {
        "DATABASE_URL": "postgres://postgres:pg@pg:5432/postgres?sslmode=disable",
        "POOL_MAX": e["POOL_MAX"],
        "LOCAL_SIZE": e["LOCAL_SIZE"],
        "LOCAL_TTL_SECONDS": e["LOCAL_TTL_SECONDS"],
        "REDIS_TTL_SECONDS": e["REDIS_TTL_SECONDS"],
        "VALUE_BYTES": e["VALUE_BYTES"],
        "READ_BYTES": e["READ_BYTES"],
        "GO_REDIS": e["GO_REDIS"],
        "STATS_DIR": "/stats",
    }
    if e["REDIS"].startswith("single"):
        env.update({"REDIS_MODE": "single", "REDIS_ADDR": "redis:6379"})
    elif e["REDIS"] == "sentinel":
        env.update({"REDIS_MODE": "sentinel", "SENTINEL_MASTER": "mymaster",
                    "SENTINEL_ADDRS": ",".join("redis-ha-announce-%d:26379" % i for i in range(3))})
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
    return lst([
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "app"}, "spec": spec},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "app"},
         "spec": {"selector": labels, "ports": [{"port": 80, "targetPort": 8080}]}},
    ])


def probe(script, delay=30, period=15):
    return {"initialDelaySeconds": delay, "periodSeconds": period, "timeoutSeconds": 15,
            "successThreshold": 1, "failureThreshold": 5,
            "exec": {"command": ["sh", "-c", "/health/" + script]}}


def redis(persist):
    labels = {"app": "redis"}
    save = "900 1" if persist else ""
    mounts = [{"name": "health", "mountPath": "/health"}]
    if persist:
        mounts.append({"name": "data", "mountPath": "/data"})
    sts = {
        "apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": {"name": "redis"},
        "spec": {"serviceName": "redis-headless", "replicas": 1, "selector": {"matchLabels": labels},
                 "template": {"metadata": {"labels": labels}, "spec": {
                     "terminationGracePeriodSeconds": 60,
                     "containers": [{
                         "name": "redis", "image": e["REDIS_TAG"], "imagePullPolicy": "IfNotPresent",
                         "args": ["redis-server", "--dir", "/data", "--save", save,
                                  "--maxmemory-policy", "volatile-lru"],
                         "ports": [{"name": "redis", "containerPort": 6379}],
                         "livenessProbe": probe("redis_liveness.sh"),
                         "readinessProbe": probe("redis_readiness.sh", int(e["READY_DELAY_SECONDS"]), int(e["READY_PERIOD_SECONDS"])),
                         "startupProbe": probe("redis_readiness.sh", int(e["READY_DELAY_SECONDS"]), int(e["READY_PERIOD_SECONDS"])),
                         "volumeMounts": mounts}],
                     "volumes": [{"name": "health", "configMap": {"name": "redis-health", "defaultMode": 0o755}}]}}},
    }
    if persist:
        sts["spec"]["volumeClaimTemplates"] = [{
            "metadata": {"name": "data"},
            "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "10Gi"}}}}]
    return lst([
        sts,
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "redis"},
         "spec": {"selector": labels, "ports": [{"port": 6379, "targetPort": 6379}]}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "redis-headless"},
         "spec": {"clusterIP": "None", "selector": labels, "ports": [{"port": 6379, "targetPort": 6379}]}},
    ])


def k6(seconds):
    env = {"BASE": "http://app", "RATE": e["RATE"], "SECONDS": str(seconds),
           "KEYS": e["KEYS"], "ZIPF_S": e["ZIPF_S"], "GOGC": e["K6_GOGC"],
           "GOMEMLIMIT": e["K6_MEMLIMIT"], "PREALLOC_VUS": e["PREALLOC_VUS"], "MAX_VUS": e["MAX_VUS"],
           "PREFILL": "0" if e["REDIS"] == "none" else "1",
           "START_RATE": e["START_RATE"], "RAMP": e["RAMP"]}
    return {
        "apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "k6"},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": seconds + 60, "template": {"metadata": {"labels": {"app": "k6"}}, "spec": {
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
    out = app()
elif what == "redis":
    out = redis(sys.argv[2] == "rdb")
elif what == "k6":
    out = k6(int(sys.argv[2]))
print(json.dumps(out, indent=1))
