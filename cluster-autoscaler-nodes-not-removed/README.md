# cluster-autoscaler-nodes-not-removed

This runs Cluster Autoscaler with the kwok cloud provider in a kind cluster whose worker nodes are kwok nodes, lowers the CPU and memory requests of the workloads so that every node is under the scale-down utilization threshold, and records what Cluster Autoscaler logs for each node (`--v=4`) and whether the node count goes down. Each condition changes one thing from the baseline, such as a Pod that cannot be evicted, a node group at its minimum size or a node that no node group owns. kube-state-metrics runs next to it, and its `/metrics` is read before and after the requests are lowered.

## Run

`.github/workflows/cluster-autoscaler-nodes-not-removed.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

The `run` job runs once per condition (a matrix), each on its own runner with its own cluster, because `--scale-down-delay-after-add` and the drain parallelism apply to the whole cluster. Each runs `./run-all.sh <condition>`, writes its `results/summary.md` to the job summary and uploads `results/` as the `cluster-autoscaler-nodes-not-removed-<condition>` artifact. The `report` job merges them, writes one `summary.md` with every condition to its job summary and uploads it as `cluster-autoscaler-nodes-not-removed-summary`.

The summary has, for each condition, a table of the predictions checked by `scripts/checks.py` and whether each held, followed by the log lines and node counts each check used. A run that did not collect the data (a step of the setup failed, the Pods of a condition were not placed one per node, the Pods were not one per node after the requests were lowered, Cluster Autoscaler did not run `loops_before` scale-down loops before the requests were lowered or none after, the Cluster Autoscaler container restarted, kube-state-metrics could not be read) fails the job with `RUN INVALID` at the top of the summary; a prediction that did not hold never fails it.

## Setup

Every condition starts from the same cluster, built by `scripts/cond.sh`.

- One kind control-plane node, tainted `NoSchedule`. kwok, kube-state-metrics and Cluster Autoscaler run on it; kindnet and kube-proxy are kept off the kwok nodes
- Three kwok nodes `ng-a-0` to `ng-a-2` (`k8s/node.yaml`), in node group `ng-a` defined by the kwok provider templates (`k8s/kwok-provider.yaml`). Every node has the same allocatable
- Two DaemonSets (`k8s/daemonsets.yaml`) and three Deployments with a `maxUnavailable: 1` PDB each (`scripts/apps.py`), one Pod of each Deployment per node. The requests start high and are lowered later, one Deployment at a time; the values are in `scripts/apps.py`. The Deployments roll out with `maxSurge: 0` and prefer nodes without a Pod of the same ReplicaSet, and the placement is checked again after the requests are lowered
- Cluster Autoscaler with the arguments of the Helm chart's default values (`--v=4`) and shorter durations (`scripts/cond.sh`)

Then Cluster Autoscaler is started and left to run `loops_before` scale-down loops, the condition's objects are created, and the requests are lowered. The node count and Cluster Autoscaler's log are recorded until the end of the condition's observation time (`window`), or until `after_removal` seconds after a node is removed where a removal is expected. These are set in `scripts/cond.sh`.

## Conditions

Each condition is one value of the matrix and one argument of `run-all.sh`. With no argument, `run-all.sh` runs them all one after another.

| Name | What differs from the baseline |
|---|---|
| `base` | Nothing |
| `p1` | A fourth kwok node, `ng-b-0`, whose node group label matches no template |
| `p2` | The node group's minimum size is the current node count |
| `p3` | `ng-a-0` has the `cluster-autoscaler.kubernetes.io/scale-down-disabled: "true"` annotation |
| `p4` | Only the memory requests are lowered |
| `p5` | Two of the Deployments have a sidecar (`restartPolicy: Always` init container) with requests. Their CPU requests before lowering are smaller by the sidecar's, so that the node totals before lowering are those of the baseline (`scripts/apps.py`) |
| `p6` | Pods without a controller on `ng-a-0` and `ng-a-2`, and a Pod owned by a custom resource on `ng-a-1` (`k8s/conditions/bare-pod.yaml`, `k8s/conditions/custom-controller*.yaml`) |
| `p7` | A Deployment in kube-system without a PDB, one Pod per node, created right before the requests are lowered (`k8s/conditions/kube-system-deployment.yaml`) |
| `p8` | `p7` with Cluster Autoscaler 1.32.1, without `--blocking-system-pod-distruption-timeout`, which 1.32.1 does not have |
| `p9` | A Deployment whose Pods have `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"` (`k8s/conditions/not-safe-to-evict.yaml`) |
| `p10` | metrics-server with the Helm chart's default Pod spec (kube-system, no PDB, an emptyDir), with three replicas so that every node has one (`k8s/conditions/metrics-server.yaml`) |
| `p11` | `p10` with a PDB (`k8s/conditions/metrics-server-pdb.yaml`) |
| `p12` | `p11` with `--skip-nodes-with-local-storage=false` |
| `p13` | A Deployment with an emptyDir of `medium: Memory` (`k8s/conditions/emptydir-memory.yaml`) |
| `p14` | A Deployment with a hostPath volume (`k8s/conditions/hostpath.yaml`) |
| `p15` | Three Deployments with `replicas: 1`, each with a `minAvailable: 1` PDB (`k8s/conditions/single-replica-pdb.yaml`) |
| `p16` | A Deployment with required podAntiAffinity on the hostname and as many replicas as nodes (`k8s/conditions/anti-affinity.yaml`) |
| `p17` | `p16` plus a Pod without a controller on `ng-a-0` |
| `p19` | `p4` plus `p15`; after the observation time, the CPU requests are lowered too and the observation runs again |

The Pods that a condition adds have small requests, so that every node stays under the threshold after the requests are lowered, and those in a Deployment are placed one per node; `results/<condition>/placement.txt` shows where they went. In `p15` the summary also checks how long a node goes without its own line after `cannot be removed:`, and in `p4` and `p5` it compares the utilization in the log with the one computed from kube-state-metrics.

## Outputs

`results/<condition>/` has:

| File | What it records |
|---|---|
| `events.log` | The time of each step of `scripts/cond.sh` |
| `ca.log` | The whole Cluster Autoscaler log (`ca-stream.log` is the same log followed during the run) |
| `cluster-autoscaler.yaml` | The Cluster Autoscaler Deployment as applied, with its arguments |
| `nodes.log` | The kwok nodes each time they change |
| `metrics-<when>.txt` | kube-state-metrics `/metrics` before the requests are lowered (`before`), after (`after`), at the end (`end`), and in `p19` before and after the second step (`stage1`, `stage2`) |
| `promql-<when>.txt` | From each `metrics-<when>.txt` (`scripts/promql.py`): for each node and resource, the sum of `kube_pod_container_resource_requests` divided by `kube_node_status_allocatable`, the same with `kube_pod_init_container_resource_requests` added, and `kube_poddisruptionbudget_status_pod_disruptions_allowed` |
| `pods-<when>.txt` | Every Pod with its node, creation time and phase, after the condition's objects are created (`pre`), after the requests are lowered (`after`) and at the end |
| `placement.txt` | How many Pods of each Deployment ended up on each node |
| `ca-status.txt`, `ca-pod.txt` | The `cluster-autoscaler-status` ConfigMap and the restart count of the Cluster Autoscaler container at the end |
| `nodes-final.txt`, `pdb-final.txt`, `events-k8s.txt` | Nodes, PDBs and Events at the end |
| `setup.log` | Downloads and kind |
| `invalid.txt` | Only when the run did not collect the data |

`results/elapsed.txt` has the seconds each condition took.

## Pinned versions

`scripts/versions.env`.

- kwok provider: node groups from templates and `NodeGroupForNode` https://github.com/kubernetes/autoscaler/blob/cluster-autoscaler-1.36.1/cluster-autoscaler/cloudprovider/kwok/kwok_provider.go , https://github.com/kubernetes/autoscaler/blob/cluster-autoscaler-1.36.1/cluster-autoscaler/cloudprovider/kwok/kwok_helpers.go
- Helm chart default arguments: https://github.com/kubernetes/autoscaler/blob/cluster-autoscaler-chart-9.59.0/cluster-autoscaler/charts/cluster-autoscaler/values.yaml#L215
- metrics-server Helm chart 3.14.0 defaults: https://github.com/kubernetes-sigs/metrics-server/blob/metrics-server-helm-chart-3.14.0/charts/metrics-server/values.yaml
