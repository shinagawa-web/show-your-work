import re
import sys
from collections import defaultdict

LINE = re.compile(r'^(\w+)\{(.*)\} (\S+)$')
LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')

containers = defaultdict(float)
inits = defaultdict(float)
alloc = {}
pdb = []
for line in sys.stdin:
    m = LINE.match(line.strip())
    if not m:
        continue
    name, labels, value = m.group(1), dict(LABEL.findall(m.group(2))), float(m.group(3))
    if name == "kube_pod_container_resource_requests" and labels.get("node"):
        containers[(labels["node"], labels["resource"])] += value
    elif name == "kube_pod_init_container_resource_requests" and labels.get("node"):
        inits[(labels["node"], labels["resource"])] += value
    elif name == "kube_node_status_allocatable":
        alloc[(labels["node"], labels["resource"])] = value
    elif name == "kube_poddisruptionbudget_status_pod_disruptions_allowed":
        pdb.append((labels["namespace"], labels["poddisruptionbudget"], value))

print("sum by (node) (kube_pod_container_resource_requests{resource=R}) / on (node) kube_node_status_allocatable{resource=R}")
print("node resource container_requests init_container_requests allocatable container_pct with_init_pct")
for node, resource in sorted(alloc):
    if resource not in ("cpu", "memory") or not node.startswith("ng-"):
        continue
    a = alloc[(node, resource)]
    c = containers[(node, resource)]
    i = inits[(node, resource)]
    print(f"{node} {resource} {c:g} {i:g} {a:g} {100 * c / a:.6g} {100 * (c + i) / a:.6g}")
print()
print("kube_poddisruptionbudget_status_pod_disruptions_allowed")
for ns, name, v in sorted(pdb):
    print(f"{ns}/{name} {v:g}")
