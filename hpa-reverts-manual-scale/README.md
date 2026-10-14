# hpa-reverts-manual-scale

This runs Deployments with a CPU HorizontalPodAutoscaler in a kind cluster with metrics-server and no load, changes `spec.replicas` (or the HPA) in a different way for each Deployment, and records who writes `spec.replicas` back and when: `spec.replicas` every second, each HPA sync from the kube-controller-manager log, the HPA status, the phase of each Pod and when it first appears in the metrics API, the Deployment's `managedFields`, and the Events.

## Run

`.github/workflows/hpa-reverts-manual-scale.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

`results/summary.md` goes to the job summary, with a table of whether the data for each prediction was collected and whether the prediction held (checked by `scripts/checks.py`), and every raw output under `results/` is uploaded as the `hpa-reverts-manual-scale-results` artifact. A run that did not collect the data (an operation exited non-zero, a section of a condition's record is missing, an HPA has no sync after the operation in the kube-controller-manager log, a new Pod of C1, or a new Pod of C10 that is not removed, never appears in the metrics API, the server-side apply of C7 was not run, no Events, a failed step) fails the job with a `RUN INVALID` line at the top of the summary; a prediction that did not hold never fails it.

## Conditions

Each condition has its own Deployment and HPA, created together and run in parallel, because an HPA keeps its past recommendations for the length of its scale-down stabilization window. The Deployments are created with client-side `kubectl apply` of `k8s/app.yaml` at `replicas: 2`, and the HPAs from `k8s/hpa.yaml` (CPU utilization, `minReplicas` 2, `maxReplicas` 5, default behavior). The operations are in `scripts/cond.sh` and start at the same moment, a fixed number of seconds (`OFFSET` in `run-all.sh`) after an HPA sync seen in the kube-controller-manager log.

| Name | What differs from the baseline |
|---|---|
| `c1-1`, `c1-2`, `c1-3` | Nothing: `kubectl scale --replicas=4` |
| `c2` | The HPA has `behavior.scaleDown.stabilizationWindowSeconds: 120` (`k8s/hpa-window120.yaml`) |
| `c3` | The Pod has an init container that sleeps for 20 seconds (`k8s/app-init.yaml`) |
| `c4` | `kubectl scale --replicas=3` |
| `c5` | `kubectl scale --replicas=1` |
| `c6` | `kubectl scale --replicas=7` |
| `c7` | Client-side `kubectl apply` of the same manifest with `replicas: 4`; after `spec.replicas` next changes, the same manifest with `kubectl apply --server-side` |
| `c8` | `minReplicas` of the HPA patched to 4 instead of scaling |
| `c9` | `behavior.scaleDown.selectPolicy: Disabled` patched into the HPA, then `kubectl scale --replicas=4` |
| `c10` | The HPA targets 80% CPU utilization (`k8s/hpa-target80.yaml`) |

Each condition is watched for `WINDOW` seconds (`scripts/cond.sh`) after its operation.

## Outputs

| File | What it records |
|---|---|
| `00-environment.txt` | kind, Kubernetes and metrics-server versions and flags, the kube-controller-manager command line, the HPAs and Deployments before the operations |
| `01-timing.txt` | The HPA sync the operations were aligned to and the time of the operations |
| `<name>.txt` | For each condition: `spec.replicas` and `managedFields` (manager, operation, subresource, time, whether the entry owns `spec.replicas`) before the operation, right after it, at every change of `spec.replicas`, after the server-side apply of `c7`, and at the end |
| `screen-c1-1.txt`, `screen-c10.txt` | `kubectl get deploy`, `kubectl get hpa` and `kubectl describe hpa` of `c1-1` and `c10` before the operation, after `kubectl scale`, after the first HPA sync, after the first HPA sync once the new Pods are in the metrics API, and after the HPA sync that follows the revert; then `kubectl events --for` the Deployment and the HPA, and `kubectl get deploy -o yaml --show-managed-fields` after `kubectl scale` and after the revert (`scripts/screens.sh`) |
| `screen-c3.txt`, `screen-c4.txt` | `kubectl get deploy` and `kubectl describe hpa` after `kubectl scale` and after the first HPA sync |
| `screen-c7.txt` | `kubectl get deploy -o yaml --show-managed-fields` after the client-side apply |
| `replicas.log` | `spec.replicas`, `status.replicas` and ready replicas of every Deployment every second (`scripts/poll.py`) |
| `hpa.log` | The HPA status (`desiredReplicas`, `currentReplicas`, utilization, `AbleToScale`, `ScalingActive`, `ScalingLimited`) each time it changes |
| `pods.log` | The phase and readiness of each Pod each time they change, and when a Pod is gone |
| `metrics.log` | When each Pod first appears in the metrics API, with the timestamp and window it reports |
| `sync.log` | Each HPA sync from the kube-controller-manager log (`scripts/syncs.py`): the proposed replicas, and whether it decided not to scale or rescaled, with the reason |
| `kube-controller-manager.log` | The whole kube-controller-manager log |
| `events.txt` | The Events of each Deployment and HPA |
| `elapsed.txt` | Seconds until the HPAs had read metrics, and in total |

kube-controller-manager runs with `--v=4` (`k8s/kind.yaml`) so that every HPA sync is logged; this changes logging only.

## Pinned versions

`scripts/versions.env`.
