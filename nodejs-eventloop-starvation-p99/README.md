# nodejs-eventloop-starvation-p99

This reproduces a Node.js service whose p99 latency jumps while system CPU stays low, because one handler blocks the event loop.

An Express app serves two endpoints from one process. `/sync-cpu?ms=<n>` repeats `JSON.parse` until `n` ms have passed, and `/light` returns at once. autocannon sends requests over 10 connections, one third to `/sync-cpu` and two thirds to `/light`, for each of `sync_ms` = 0, 50, 100, 150 and 200. Each step has a 10s warmup and a 30s measurement.

## What is recorded

`loadgen/ramp.js` writes one CSV row per endpoint and `sync_ms`:

- p50 and p99 latency, request count and non-2xx count, measured by the load generator per endpoint
- `cpu_pct`: the app process's CPU time over a 1s window, divided by the number of CPUs (`app/metrics.js`)
- `lag_p99`: the p99 of `monitorEventLoopDelay({ resolution: 20 })` over a 1s window, sampled every 3s during the measurement, maximum taken

## Conditions

| CI job | What it runs |
|---|---|
| No shedding | the app as is |
| Shedding threshold=150ms | the app with `SHED_THRESHOLD_MS=150`: `app/middleware/shed-eventloop.js` returns 503 with `Retry-After: 1` while its own 100ms `setTimeout` lag is above the threshold |
| V8 CPU profile | `experiments/02-profiling/run.sh`: the app under `node --prof`, `/sync-cpu?ms=200` over 10 connections for 20s, then `node --prof-process` |

The app runs in Docker with `cpus: 2.0` (`compose.yaml`), except for the profile, which runs it directly on the host.

## Run it

On a host with Docker and Node.js 22:

```
cd nodejs-eventloop-starvation-p99
npm install && (cd app && npm install)
docker compose --profile base up -d --build
CSV_OUT=results-baseline.csv node loadgen/ramp.js exp01
node analyze/plot.js results-baseline.csv
docker compose --profile base down
```

For the shedding condition, start the app with `SHED_THRESHOLD_MS=150 docker compose --profile base up -d --build`. `CONCURRENCY`, `DURATION` and `WARMUP` change the load.

## Results

Each CI job's summary has the table for that condition. Artifacts carry the CSV and the profile.
