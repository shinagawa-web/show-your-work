# istio-outlier-slow-pod

This runs one Service with three Pods and one caller Pod in a kind cluster with Istio sidecars, makes one Pod slow or makes it return 500, and records whether the caller's Envoy ejects that Pod under Outlier Detection. Each condition changes one thing from the baseline: the Pod state, `maxEjectionPercent`, a timeout in the k6 HTTP client, or the route timeout in a VirtualService.

## Run

`.github/workflows/istio-outlier-slow-pod.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

| Job | What it runs |
|---|---|
| `3-1` | `./run-all.sh 5xx-default 5xx-34 slow-34 slow-client-200ms slow-body-200ms`. `3-1` is the section of the article the job supports |
| `3-2` | `./run-all.sh route-20ms route-200ms route-1000ms`. `3-2` is the section of the article the job supports |
| `report` | Merges the results of the two jobs into `results/summary.md` (`scripts/report.py`) |

Each of `3-1` and `3-2` writes its own `results/summary.md` to its job summary and uploads `results/` as an `istio-outlier-slow-pod-<job>` artifact. The `report` job writes the merged `results/summary.md` to its job summary, uploads it as the `istio-outlier-slow-pod-summary` artifact, and on a push posts it as a comment on the open pull request for the branch, replacing the earlier one.

A job fails with a `RUN INVALID` line at the top of its summary when the setup failed, the caller's Envoy did not get the expected Outlier Detection or route timeout (`scripts/preflight.py`), k6 dropped iterations or sent a number of requests more than one second's worth away from the target, the access log of the caller's sidecar did not have one line for each k6 request, the Envoy admin stats were read too few times, or the `outlier_detection` stats or the `OUTLIER CHECK` column were missing. A Pod that is or is not ejected does not fail the job.

## Setup

Every condition starts from a new `demo` namespace in the same cluster, built by `scripts/cond.sh`, and the rates and durations are in `scripts/params.env`.

- One kind node with Istio installed by `istioctl` (`profile=minimal`), with the JSON access log on stdout (`scripts/setup.sh`)
- The Deployments `app-normal` (two Pods) and `app-bad` (one Pod) under one Service (`k8s/app.yaml`). The server in `app/` waits `DELAY_MS` and returns `STATUS`, and with `BODY_DELAY_MS` above 0 it flushes the headers and sends the body after that many milliseconds
- A DestinationRule with `consecutive5xxErrors: 5` (`k8s/destinationrule.yaml`), and in the `route-` conditions and `slow-body-200ms` a VirtualService (`k8s/virtualservice.yaml`)
- A `k6` Pod with a sidecar and a `proxyStatsMatcher` annotation for `outlier_detection` (`k8s/k6.yaml`). It runs `k6/load.js`, which has a `constant-arrival-rate` load and reads the Envoy admin stats on `127.0.0.1:15000` five times a second

`scripts/preflight.py` waits until the caller's Envoy has the expected configuration and three healthy endpoints before the load starts.

The `route-` conditions then wait `QUIET` seconds before the load. Envoy holds back an ejection from its load balancers for `update_merge_window` when the ejection comes shortly after another update of the same cluster, and the Pods starting are such an update. The wait keeps that update away from the ejection, so that the requests sent to the ejected Pod are those of the ejection itself.

## Conditions

Each condition is one argument of `run-all.sh`. With no argument, `run-all.sh` runs them all one after another.

| Name | Pod `app-bad` | `maxEjectionPercent` | Timeout |
|---|---|---|---|
| `5xx-default` | Returns 500 | Default | None |
| `5xx-34` | Returns 500 | 34 | None |
| `slow-34` | Slow | 34 | None. This is the baseline |
| `slow-client-200ms` | Slow | 34 | 200 ms in the k6 HTTP client |
| `slow-body-200ms` | Headers at once, body slow | 34 | 200 ms on the route |
| `route-20ms` | Slow | 34 | 20 ms on the route |
| `route-200ms` | Slow | 34 | 200 ms on the route |
| `route-1000ms` | Slow | 34 | 1000 ms on the route |

## Results

`results/<condition>/` holds:

| File | What it is |
|---|---|
| `k6.json`, `k6.log` | One k6 sample per request and per read of the admin stats, and the k6 output |
| `proxy.log` | The access log of the caller's sidecar, one line per request with the upstream host and the response flags |
| `endpoint-mid.txt` | `istioctl proxy-config endpoint` during the load |
| `clusters-mid.txt` | The Envoy admin `clusters` output during the load |
| `stats-end.txt` | The `outlier_detection` stats after the load |
| `cluster.json`, `routes.json` | The Envoy cluster and routes checked before the load |
| `pods.json` | The Pods of the condition |
| `server_info.json` | The Envoy `server_info` of the caller's sidecar |
| `metrics.json`, `cond.env` | What `scripts/report.py` reads, written by `scripts/measure.py` and `scripts/cond.sh` |

`results/elapsed.txt` has the seconds for the setup and each condition.

## Pinned versions

`scripts/versions.env`.

`update_merge_window` is applied in `ClusterManagerImpl::scheduleUpdate` in `source/common/upstream/cluster_manager_impl.cc` of Envoy at commit `68eef0a0e0600aa47d4d9c03ae4a7866f78222c3`, the commit Istio pins.
