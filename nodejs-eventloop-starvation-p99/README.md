# nodejs-eventloop-starvation-p99

This reproduces a Node.js service whose p99 latency jumps while system CPU stays low, because one handler blocks the event loop. One Express process serves `/sync-cpu?ms=<n>`, which repeats `JSON.parse` until `n` ms have passed, and `/light`, which returns at once. autocannon sends a mix of both for each `sync_ms` step.

## Run

`.github/workflows/nodejs-eventloop-starvation-p99.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

| Job | What it runs |
|---|---|
| `run (baseline)` | the app in Docker as is |
| `run (shed-<ms>)` | the app with `SHED_THRESHOLD_MS=<ms>`: `app/middleware/shed-eventloop.js` returns 503 with `Retry-After` while its own `setTimeout` lag is above the threshold |
| `run (profile)` | `scripts/profile.sh`: the app on the runner under `node --prof` with load on `/sync-cpu`, then `node --prof-process` |

`results/<condition>/summary.md` goes to the job summary, and `results/` is uploaded as the `nodejs-eventloop-starvation-p99-<condition>` artifact.

When the measurement did not happen, the summary ends with a `RUN INVALID` line and the job fails: the app did not answer, a step completed no request, `/metrics` could not be read, the baseline got non-2xx responses, or the profile is empty (`scripts/check.js`).

## CSV columns

`results/<condition>/results.csv` has one row per endpoint (`condition`) and `sync_ms` (`knob`). The load mix, steps, warmup and measurement time are in `loadgen/scenarios/exp01.js`, and the container limits in `docker-compose.yml`.

- `p50`, `p99`, `total`, `non2xx`: measured by the load generator per endpoint
- `cpu_pct`: the app process's CPU time over the last second, divided by the number of CPUs (`app/metrics.js`)
- `lag_p99`: the p99 of `monitorEventLoopDelay` over the last second (`app/metrics.js`), sampled during the measurement, maximum taken
- `errors` and `timeouts` are not measured and are always 0

## Pinned versions

Dependencies are locked in `package-lock.json` and `app/package-lock.json`. Node is in `.node-version` and `app/Dockerfile`.
