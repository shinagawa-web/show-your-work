# local-cache-cold-pods

A Go service keeps its cache in the Pod or in Redis, in front of PostgreSQL. Under a constant request rate the app Pods or Redis are restarted, and the app counts the requests that the cache did not answer and the time Redis could not be read. k6 records the latency of every request.

## Run

`.github/workflows/local-cache-cold-pods.yml` runs one job per condition. Each job runs `./run-all.sh <condition>`. Without a condition, `./run-all.sh` runs all of them.

| condition | cache | operation after the caches are filled |
|---|---|---|
| `app-restart-local` | local cache only | `kubectl rollout restart` of the app |
| `app-restart-redis` | Redis only, single instance with PVC and `save 900 1` | `kubectl rollout restart` of the app |
| `redis-restart-none` | Redis only, single instance with `save ""` and no PVC | `kubectl rollout restart` of Redis |
| `redis-restart-none-probe` | as `redis-restart-none`, with the readiness and startup probes of Redis at `initialDelaySeconds` 1 and `periodSeconds` 1 | `kubectl rollout restart` of Redis |
| `redis-restart-rdb` | Redis only, single instance with PVC and `save 900 1` | `kubectl rollout restart` of Redis |
| `redis-restart-rdb-probe` | as `redis-restart-rdb`, with the readiness and startup probes of Redis at `initialDelaySeconds` 1 and `periodSeconds` 1 | `kubectl rollout restart` of Redis |
| `sentinel-restart` | Redis only, redis-ha chart | `kubectl rollout restart` of the redis-ha StatefulSet with a partition of 2, with `redis-ha-server-2` as master, so that only the master is replaced |

Each job is named after its article section and condition. Each condition runs under `timeout 300`, writes `results/<condition>/` and adds its summary to the job summary. Each job uploads `results/` as an artifact. The `summary` job merges them with `scripts/report.py` into `summary.md`, adds it to its job summary, and posts it as a pull request comment, replacing the previous one.

## Setup

- `scripts/cpus.sh` reads `thread_siblings_list` and gives the first half of the physical cores to the kind node and the second half to PostgreSQL
- kind runs a single node limited to `NODE_CPUS` with `docker update --cpuset-cpus`. The app Pods, Redis and k6 run there
- PostgreSQL runs outside kind as a Docker container on `PG_CPUS` (`--cpuset-cpus`, `--network kind`) with the default configuration. The Pods reach it through the Service `pg`, whose Endpoints hold the container's IP
- `app/main.go` serves `/item?id=<key>`. It reads the cache of the condition, the local cache (`hashicorp/golang-lru/v2` `expirable`) or Redis (`redis/go-redis`), then PostgreSQL through a `pgxpool` pool with `MaxConns` from `POOL_MAX`. A value read from PostgreSQL is written back to the cache. A failed Redis read is counted and the request goes to PostgreSQL. go-redis keeps its default timeouts and retries. The redis-ha condition uses `NewFailoverClient`
- Every value in Redis is `VALUE_BYTES` long, so that RDB loading shows in the time Redis is stopped. It is a JSON header of 256 bytes followed by incompressible bytes. The app reads `READ_BYTES` from the start of the value with `GETRANGE`
- Each app Pod also runs a probe with its own go-redis client: one read every 100 ms, a 300 ms limit and no retries. Its start and end of failure, written to `/stats/<pod>.events`, give the time Redis was stopped.
- Every second each Pod appends its counters to `/stats/<pod>.jsonl` on the node
- The single instance is a StatefulSet of the official image with the probes of the chart: `k8s/redis_readiness.sh`, `k8s/redis_liveness.sh` and the same delays, periods and thresholds. The PVC uses the default StorageClass of kind
- The redis-ha chart keeps its defaults apart from `k8s/redis-ha-values.yaml`. `podManagementPolicy: Parallel` starts the 3 servers together, so that the setup fits the job. The `init` container of each server waits for Sentinels to answer before it starts Redis
- The chart renders only the `type` of the update strategy, so `sentinel-restart` sets the partition with `kubectl patch`. Clients read the master only, so the replicas are not replaced. The chart's `preStop` failover runs as usual
- k6 runs in the cluster with `ramping-arrival-rate` and `noConnectionReuse: true`, so that each request opens a new connection. The rate rises from `START_RATE` to `RATE` over `RAMP` seconds, then stays at `RATE`. Keys follow a Zipf distribution. In the conditions with Redis, k6 first reads every key once in `setup()`, one at a time, so that Redis is filled before the load starts. The operation runs `WARM` seconds after k6 starts. `scripts/watch.py` stops k6 once, for `QUIET_SECONDS` seconds, the reads sent to PostgreSQL stay below `QUIET_READS` per second, no read is in flight, no Redis read failed and the CPU of the PostgreSQL container is within `QUIET_DB_CPU` points of its value before the operation, or `MAX_OBS` seconds after the operation
- `scripts/poll.py` records the Pods every 0.5 s and the CPU usage of the PostgreSQL container and the node container every second

`scripts/params.env` holds the request rates, number of keys, Zipf exponent, cache sizes and TTLs, value size, pool size, table sizes, VUs, warm-up seconds and the stop conditions. `READY_DELAY_SECONDS` and `READY_PERIOD_SECONDS` are the `initialDelaySeconds` and `periodSeconds` of the readiness and startup probes of the single instance. Their values match the chart, apart from `redis-restart-none-probe` and `redis-restart-rdb-probe`.

## Output

| file | content |
|---|---|
| `summary.md` | the numbers per article section |
| `<condition>/metrics.json` | the values `scripts/report.py` prints, and others to look into a run |
| `<condition>/timeseries.tsv` | per-second requests, hits, misses, failed Redis reads and writes, k6 p50 and p99 and CPU |
| `<condition>/stats/` | per-second counters and failure events of each Pod |
| `<condition>/k6/` | `k6.log` with one line per request, `summary.json` |
| `<condition>/logs/` | Redis and Sentinel logs with timestamps, app logs |
| `<condition>/redis-before.txt`, `redis-after.txt` | keys, replication, persistence and memory of each Redis server |
| `<condition>/pods.jsonl`, `cpu.jsonl` | Pod states and CPU samples |
| `<condition>/timeline.txt` | when k6 started, the operation, the end of the rollout and of the load |
| `<condition>/pgbench.txt` | `pgbench` with the app's query, run before the app starts |

## Pinned versions

`scripts/versions.env` and `app/go.mod`.
