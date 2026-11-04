# local-cache-cold-pods

A Go service keeps a local cache in each Pod in front of PostgreSQL. Four Pods run with filled caches under a constant request rate, then Pods with empty caches are added with `kubectl scale`, or all Pods are replaced with `kubectl rollout restart`. The app counts the reads it sends to PostgreSQL per second, and k6 records the latency of every request.

## Run

`.github/workflows/local-cache-cold-pods.yml` runs one job per condition. Each job runs `./run-all.sh <condition>`:

| condition | operation after the caches of the 4 Pods are filled |
|---|---|
| `add-1`, `add-4` | `kubectl scale` to 5 or 8 replicas, then back to 4 after the load |
| `restart-default` | `kubectl rollout restart` with the default rolling update strategy |
| `restart-default-ready` | `kubectl rollout restart` with the default `maxSurge` and `maxUnavailable` and `minReadySeconds` from `MIN_READY` |
| `restart-surge1-noready` | `kubectl rollout restart` with `maxSurge: 1`, `maxUnavailable: 0` and `minReadySeconds: 0` |
| `restart-surge1` | `kubectl rollout restart` with `maxSurge: 1`, `maxUnavailable: 0` and `minReadySeconds` from `MIN_READY` |

Each job writes its own `results/summary.md` to the job summary and uploads `results/` as the artifact `local-cache-cold-pods-<condition>`. The `summary` job merges the artifacts with `scripts/report.py`, writes the result to its job summary, and posts it as a comment on the pull request of the branch, replacing the previous one.

## Setup

- kind runs a single node whose container is limited to the CPUs in `NODE_CPUS` with `docker update --cpuset-cpus`. The app Pods and k6 run there
- PostgreSQL runs outside kind as a Docker container on the CPUs in `PG_CPUS` only (`--cpuset-cpus`, `--network kind`) with the default configuration. The Pods reach it through the Service `pg`, whose Endpoints hold the container's IP
- `app/main.go` serves `/item?id=<key>`. A key missing from the local cache (`hashicorp/golang-lru/v2` `expirable`) is read from PostgreSQL through a `pgxpool` pool with `MaxConns` from `POOL_MAX`, with a query that joins `items`, `reviews` and `users`. `/ready` answers 200 once every connection of the pool has been opened
- Every second each Pod appends its counters (requests, cache hits, reads sent to PostgreSQL, reads that started while another read of the same key was in flight in the same Pod, `pgxpool` `Stat()`) to `/stats/<pod>.jsonl` on the node
- k6 runs in the cluster with `ramping-arrival-rate` and `noConnectionReuse: true`, so that each request opens a new connection to the Service and is balanced across all Ready Pods. The rate rises from `START_RATE` to `RATE` over `RAMP` seconds while the caches of the first 4 Pods fill, then stays at `RATE`. The operation runs `WARM` seconds after k6 starts. After `kubectl rollout status` returns, `scripts/watch.py` stops k6 once, for `QUIET_SECONDS` seconds, the reads sent to PostgreSQL stay below `QUIET_READS` per second, no read is in flight in any Pod and the CPU of the PostgreSQL container is within `QUIET_DB_CPU` points of its value before the operation, or `MAX_OBS` seconds after the operation. Keys follow a Zipf distribution
- `scripts/poll.py` records the app Pods every 0.5 s and the CPU usage of the PostgreSQL container, the node container and each CPU every second

`scripts/params.env` holds the request rates, number of keys, Zipf exponent, cache size and TTL, pool size, table sizes, warm-up seconds, `MIN_READY` and the stop conditions.

## Output

| file | content |
|---|---|
| `summary.md` | the condition's values and a per-second table from the operation on |
| `metrics.json` | the values used by `scripts/report.py` |
| `timeseries.tsv` | per-second requests, DB reads, hit rates, k6 p50 and p99, pool waits and CPU |
| `stats/` | per-second counters of each Pod |
| `k6/` | `k6.log` with one line per request, `summary.json` |
| `pods.jsonl`, `cpu.jsonl` | Pod states and CPU samples |
| `timeline.txt` | when k6 started, the operation, the end of the rollout and of the load |
| `initial-pods.txt`, `final-pods.txt` | app Pods before the load and after scaling back to 4 |
| `pgbench.txt` | `pgbench` with the app's query, run before the app starts |
| `00-environment.txt` | versions, CPU sets, PostgreSQL settings, the query plan |

## Pinned versions

`scripts/versions.env` and `app/go.mod`.
