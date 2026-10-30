# liveness-probe-overload-restart

This runs a gunicorn server with a fixed number of sync workers in a kind cluster, with its liveness and readiness probes served by the same workers as the normal requests, loads it above and then back below its capacity, and records the accept queue of each Pod, the probe results, the restarts and the Events.

## Run

`.github/workflows/liveness-probe-overload-restart.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

| Job | What it runs |
|---|---|
| `<section> (<condition>-<run>)` | `./run-all.sh <condition>`, once for `capacity` and three times for each of `P1` to `P5`, each job on its own runner and cluster. `<section>` is the section of the article the condition supports |
| `summary` | Merges the results of the other jobs into `results/summary.md` (`scripts/report.py`) |

Each run job writes its own `results/summary.md` to its job summary and uploads `results/` as the `liveness-probe-overload-restart-<condition>-<run>` artifact. The `summary` job writes the merged `results/summary.md` to its job summary, uploads it as the `liveness-probe-overload-restart-summary` artifact, and on a push posts it as a comment on the open pull request for the branch, replacing the earlier one.

A run job fails with a `RUN INVALID` line at the top of its summary when k6 dropped iterations, a load stage missed its target rate by more than 2%, a Pod was on the wrong node, the Event watch or the EndpointSlice watch reported an error, the Event watch missed a count, the kubelet probe metrics were missing, the `/work` requests a Pod processed per second, counted in the seconds of the 1.2C stage in which its Recv-Q was above 0, were 5% or more away from `C`/3, or, in `capacity`, the measured capacity was 5% or more away from `C` in `scripts/capacity.env`. A run without restarts does not fail.

## What one run does

`scripts/setup.sh` creates a kind cluster with a control-plane node and a worker node from `k8s/kind.yaml`, builds the server from `app/`, and loads it and the k6 image into the nodes. The control-plane taint is removed so that k6 runs there, and the server Pods are pinned to the worker.

The server is a Deployment of 3 Pods (`scripts/render.py`), each running gunicorn as PID 1 with 4 sync workers and the default backlog. A request to any path other than `/livez` and `/readyz` sleeps 20 ms. `/livez` and `/readyz` return at once, on the same workers, and every request is logged with the times its processing started and ended. The liveness probe uses `/livez` and the readiness probe `/readyz`, both with `timeoutSeconds: 1`, `periodSeconds: 10` and `failureThreshold: 3`. The Pods have CPU and memory requests and no limits. They are started one at a time, each 3 s after the previous container is running, so that their probe periods are not aligned.

k6 (`k6/load.js`) sends GET requests to the ClusterIP Service at a fixed arrival rate per stage, with a 2 s timeout and 1500 VUs. The rates are fractions of `C` (`scripts/conditions.py`). While it runs:

- `scripts/poll.py` reads the Pods and the EndpointSlice every second, `/metrics/probes` of the worker's kubelet every second, and watches the Events and the EndpointSlice from the start of the run. When a container restarts, it saves `kubectl logs --previous`
- `scripts/recvq.sh` runs `ss -ltn` in each server Pod's network namespace from the worker node every second, and reads `TcpExtListenOverflows` and `TcpExtListenDrops` of the same namespace

`scripts/analyze.py` turns one run into `metrics.json` and `summary.md`. t = 0 is the end of the first stage. A restart is placed at the time the Killing Event was received. The wait of a probe is from its scheduled time to the time the app started processing it: liveness probes are scheduled every `periodSeconds` from a phase taken from the probes logged before the 1.2C stage, and readiness probes every `periodSeconds` from the first readiness probe logged in each container, restarting from the extra probe the kubelet runs when the third failure in a row makes the Pod unready. Each scheduled probe is paired with its result from `prober_probe_total`.

## Conditions

| Condition | Name in `results/summary.md` | What changes from `P1` |
|---|---|---|
| `capacity` | capacity measurement | 60 VUs in a closed loop for 40 s instead of the stages, to measure the throughput of the 3 Pods |
| `P1` | baseline (no change) | Nothing: 0.75C for 15 s, 1.2C for 60 s, 0.9C for 105 s |
| `P2` | no 1.2C stage | 0.75C for 15 s, then 0.9C for 165 s |
| `P3` | probes on a second port (8081) | gunicorn also binds 8081 and both probes use it. The workers are the same |
| `P4` | liveness timeoutSeconds 5 | The liveness probe has `timeoutSeconds: 5`. The readiness probe is unchanged |
| `P5` | no liveness probe | The liveness probe is removed. The readiness probe is unchanged |

## Outputs

| File | What it records |
|---|---|
| `00-environment.txt` | kind, Kubernetes, gunicorn and Python versions, the kernel of the worker node, `somaxconn` in a Pod, `C`, the stages, the nodes, the Pods and the Pod spec |
| `timeline.txt` | When each replica was added and its container was running, and the start and end of the measurement |
| `pods.jsonl`, `eps.jsonl` | The Pods (restart count, Ready, state, last state) and the EndpointSlice endpoints every second |
| `eps-watch.jsonl`, `eps-watch.json` | The EndpointSlice watch with the number of Ready endpoints after each change, and its errors and reconnects |
| `probes.txt` | `prober_probe_total` from the kubelet every second |
| `events.jsonl`, `events-watch.json`, `events-final.json` | The Event watch, its errors and reconnects, and the Events at the end |
| `recvq.txt` | Recv-Q and the backlog of each listen socket of each server Pod, and `ListenOverflows` and `ListenDrops` of its network namespace (`NETSTAT` lines), every second |
| `k6/k6.log`, `k6/summary.json` | One line per request (start, duration, status, error) and the k6 end-of-test summary |
| `metrics.json` | What `scripts/report.py` reads, including each scheduled probe with its wait and the kubelet's result |
| `logs/`, `previous/` | The server logs of the containers running at the end, and of each restarted container |
| `kubelet.log` | The kubelet journal of the worker node |
| `elapsed.txt` | Seconds for the setup and in total |

## Pinned versions

`scripts/versions.env`. gunicorn is installed from the source archive of its GitHub tag, checked against the SHA-256 in the same file, because that version is not on PyPI.
