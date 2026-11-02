# probe-wait-by-server-type

This runs the same overload against five kinds of HTTP server in a kind cluster (gunicorn with sync workers, Spring Boot on Tomcat with its default thread pool, the same Spring Boot with virtual threads, Node.js `node:http`, and Go `net/http`), and records how long a probe request waits in each, the values that show the waiting (Recv-Q, Tomcat metrics, event loop delay, goroutines, open file descriptors), what the default liveness and readiness probes do, and what the client sees.

## Run

`.github/workflows/probe-wait-by-server-type.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

| Job | What it runs |
|---|---|
| `build` | `./run-all.sh build`, which builds the Spring Boot image once into `.images/` and passes it to the other jobs as the `probe-wait-by-server-type-image` artifact. The `tomcat` and `tomcat-virtual` jobs load it, and the other jobs build their own image |
| `1, 3, 4 (<server>)` | `./run-all.sh <server>`, once for each of `gunicorn`, `tomcat`, `tomcat-virtual`, `node` and `go`, each job on its own runner and cluster. `1, 3, 4` are the sections of the article the job supports |
| `report` | Merges the results of the server jobs into `results/summary.md` (`scripts/report.py`) |

Each server job writes its own `results/summary.md` to its job summary and uploads `results/` as the `probe-wait-by-server-type-run-<server>` artifact. The `report` job writes the merged `results/summary.md` to its job summary, uploads it as the `probe-wait-by-server-type-summary` artifact, and on a push posts it as a comment on the open pull request for the branch, replacing the earlier one.

A server job fails with a `RUN INVALID` line at the top of its summary when k6 dropped iterations, a load stage missed its target rate by more than 2%, a Pod was on the wrong node, the Event watch or the EndpointSlice watch reported an error, the Event watch missed a count, the kubelet probe metrics were missing, the prober or the server recorded nothing for a Pod, or the `/work` requests a Pod processed per second, counted in the seconds of the 1.2C stage in which requests were waiting, were 5% or more away from the per-Pod rate for that server in `scripts/capacity.env`. Requests are waiting when the listen socket's Recv-Q is above 0 for gunicorn, and when a request is waiting for the semaphore for the other servers. A run without restarts does not fail.

## What one run does

`scripts/setup.sh` creates a kind cluster with a control-plane node and a worker node from `k8s/kind.yaml`, builds the server image with `scripts/build.sh`, or loads it from `.images/` when the `build` job made it, and loads it into the worker and the k6 and prober images into the control-plane node. The control-plane taint is removed so that k6 and the prober run there, and the server Pods are pinned to the worker.

The server is a Deployment of 3 Pods (`scripts/render.py`, `scripts/servers.py`) with the server as PID 1. Every server serves `/work` and a probe path on port 8080:

- `/work` takes a first-come-first-served semaphore with 4 permits, sleeps `WORK_MS` (`scripts/capacity.env`) inside it, and returns 200. It keeps waiting when the client goes away
- The probe path does not touch the semaphore. The liveness and readiness probes use it with `timeoutSeconds: 1`, `periodSeconds: 10` and `failureThreshold: 3`
- Once a second, each server process prints a `stat` line with the `/work` requests that arrived and finished in that second, the requests waiting for the semaphore, the requests in the handler, and values of its own: `tomcat.threads.busy`, `tomcat.connections.current`, the executor queue length and pool size (Spring Boot, from a platform thread outside Tomcat), the event loop delay from `monitorEventLoopDelay` with a 10 ms resolution and the open connections (Node.js), and the goroutines and open connections (Go)

| Server | Source | Probe path | What differs |
|---|---|---|---|
| `gunicorn` | `servers/gunicorn/` | `/healthz` | 4 sync workers, `--preload` so that the workers share the semaphore |
| `tomcat` | `servers/spring/` | `/actuator/health/liveness`, `/actuator/health/readiness` | Spring Boot defaults, with `server.tomcat.mbeanregistry.enabled=true` so that the Tomcat metrics exist |
| `tomcat-virtual` | `servers/spring/` | same as `tomcat` | `SPRING_THREADS_VIRTUAL_ENABLED=true` |
| `node` | `servers/node/` | `/healthz` | A SIGTERM handler that exits, because Node.js as PID 1 ignores SIGTERM otherwise |
| `go` | `servers/go/` | `/healthz` | The semaphore is a buffered channel |

The Pods have CPU and memory requests and no limits. They are started one at a time, each 3 s after the previous container is running, so that their probe periods are not aligned.

k6 (`k6/load.js`) sends GET `/work` to the ClusterIP Service at a fixed arrival rate per stage, with a new connection per request, a 2 s timeout and 2000 VUs. The stages are fractions of `C` = 600 rps for the 3 Pods (`scripts/conditions.py`): 0.75C for 15 s, 1.2C for 60 s, 0.75C for 105 s. While it runs:

- The prober (`prober/prober.py`), in a Pod on the control-plane node, sends GET to the liveness probe path of each server Pod IP every second, each on a new connection with a 30 s timeout, and records how long it took
- `scripts/poll.py` reads the Pods and the EndpointSlice every second, `/metrics/probes` of the worker's kubelet every 5 s, and watches the Events and the EndpointSlice from the start of the run. When a container restarts, it saves `kubectl logs --previous`
- `scripts/nodewatch.sh` runs `ss -ltn` in each server Pod's network namespace from the worker node every second, reads `ListenOverflows` and `ListenDrops` of the same namespace, counts the entries in `/proc/<pid>/fd` of every process in the container's cgroup, and reads `usage_usec` of the container's cgroup and the `cpu` line of `/proc/stat`

`scripts/analyze.py` turns one run into `metrics.json` and `summary.md`. t = 0 is the start of the 1.2C stage.

`./run-all.sh <server> capacity` runs the same cluster with 60 VUs in a closed loop for 40 s instead of the stages, to measure the throughput of the 3 Pods. It is how the values in `scripts/capacity.env` were measured, and no workflow job runs it.

## Outputs

| File | What it records |
|---|---|
| `00-environment.txt` | kind, Kubernetes and server versions, the kernel of the worker node, `somaxconn`, `WORK_MS` and the per-Pod rate, the stages, the nodes, the Pods and the Pod spec |
| `timeline.txt` | When each replica was added and its container was running, and the start and end of the measurement |
| `prober.log` | One line per probe request of the prober: Pod, send time, seconds until the response was read, status or error |
| `pods.jsonl`, `eps.jsonl` | The Pods (restart count, Ready, state, last state) and the EndpointSlice endpoints every second |
| `eps-watch.jsonl`, `eps-watch.json` | The EndpointSlice watch with the number of Ready endpoints after each change, and its errors and reconnects |
| `probes.txt` | `prober_probe_total` from the kubelet every 5 s |
| `events.jsonl`, `events-watch.json`, `events-final.json` | The Event watch, its errors and reconnects, and the Events at the end |
| `node.txt` | Recv-Q and the backlog of each listen socket of each server Pod, `ListenOverflows` and `ListenDrops` of its network namespace (`NETSTAT` lines), the open file descriptors and processes of the container (`FD` lines), the CPU time of the container (`CPU` lines) and of the VM (`HOSTCPU` lines), every second |
| `k6/k6.log`, `k6/summary.json` | One line per request (start, duration, status, error) and the k6 end-of-test summary |
| `logs/`, `previous/` | The server logs, with the `stat` lines, of the containers running at the end, and of each restarted container |
| `metrics.json` | What `scripts/report.py` reads |
| `kubelet.log` | The kubelet journal of the worker node |
| `elapsed.txt` | Seconds for the setup and in total |

## Pinned versions

`scripts/versions.env`, `servers/spring/pom.xml` (Spring Boot) and `servers/go/go.mod`. gunicorn is installed from the source archive of its GitHub tag, checked against the SHA-256 in `scripts/versions.env`, because that version is not on PyPI.
