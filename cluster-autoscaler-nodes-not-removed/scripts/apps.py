import json
import sys

APPS = [
    ("web", {"before": ("1000m", "3Gi"), "after": ("500m", "1536Mi")}),
    ("api", {"before": ("1000m", "3Gi"), "after": ("500m", "1536Mi")}),
    ("worker", {"before": ("600m", "3Gi"), "after": ("400m", "1536Mi")}),
]
SIDECAR_APPS = {"web", "api"}
SIDECAR_REQUESTS = {"cpu": "300m", "memory": "128Mi"}
SIDECAR_APP_CPU_BEFORE = "700m"


def requests(sizes, cpu_phase, mem_phase):
    return {"cpu": sizes[cpu_phase][0], "memory": sizes[mem_phase][1]}


def deployment(name, reqs, pause, sidecar):
    spec = {
        "affinity": {
            "podAntiAffinity": {
                "preferredDuringSchedulingIgnoredDuringExecution": [
                    {
                        "weight": 100,
                        "podAffinityTerm": {
                            "topologyKey": "kubernetes.io/hostname",
                            "labelSelector": {"matchLabels": {"app": name}},
                            "matchLabelKeys": ["pod-template-hash"],
                        },
                    }
                ]
            }
        },
        "containers": [{"name": name, "image": pause, "resources": {"requests": reqs}}],
    }
    if sidecar and name in SIDECAR_APPS:
        spec["initContainers"] = [
            {"name": "proxy", "image": pause, "restartPolicy": "Always", "resources": {"requests": SIDECAR_REQUESTS}}
        ]
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": "default"},
        "spec": {
            "replicas": 3,
            "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1}},
            "selector": {"matchLabels": {"app": name}},
            "template": {"metadata": {"labels": {"app": name}}, "spec": spec},
        },
    }


def pdb(name):
    return {
        "apiVersion": "policy/v1",
        "kind": "PodDisruptionBudget",
        "metadata": {"name": name, "namespace": "default"},
        "spec": {"maxUnavailable": 1, "selector": {"matchLabels": {"app": name}}},
    }


def main():
    cpu_phase, mem_phase, sidecar, only, pause = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4], sys.argv[5]
    items = []
    for name, sizes in APPS:
        if only not in ("all", name):
            continue
        reqs = requests(sizes, cpu_phase, mem_phase)
        if sidecar and name in SIDECAR_APPS and cpu_phase == "before":
            reqs["cpu"] = SIDECAR_APP_CPU_BEFORE
        items.append(deployment(name, reqs, pause, sidecar))
        items.append(pdb(name))
    json.dump({"apiVersion": "v1", "kind": "List", "items": items}, sys.stdout, indent=1)


main()
