# go-goroutine-pileup-memory

Verification in progress. This measures a Go HTTP server whose handlers wait on a slow downstream: when requests arrive faster than they complete, goroutines pile up and memory grows while CPU stays low.

## Files

| File | What it is |
|---|---|
| `main.go`, `Dockerfile`, `go.mod` | The server: `/light`, `/sync-io` (waits), `/sync-cpu` (spins, per `ARM`), `/metrics` |
| `compose.yaml` | The server in Docker with `cpus: 2.0` and `mem_limit: 128m` |
| `loadgen/open-loop.js` | Open-loop load at a fixed `RATE` against `/sync-io` |
| `loadgen/ramp-go.js`, `loadgen/scenarios/go01.js` | Closed-loop load over `sync_ms` steps, one CSV row per endpoint |
| `run-rss.sh` | Runs the server on the host and records the goroutine count and RSS over time |
| `run-oom.sh` | Runs the server in Docker under `mem_limit` until it is OOM killed |
| `run-oom-lima.sh`, `run-latency-lima.sh` | The same in a Linux VM (Lima), the second one with a latency probe on `/light` |
| `results/` | Time series recorded so far |
| `NOTES.md` | Rounds of the experiment and what each one changed |

## CI

The workflow runs condition A (unlimited goroutines) with the closed-loop load and puts the table in the job summary.
