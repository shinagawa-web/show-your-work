# nginx-accept-queue-overflow

Puts nginx (an explicit `upstream` block, no `keepalive` directive) in front of one Node.js HTTP server whose listen backlog is set by an argument, sends a constant 200 requests per second, stops the server process from outside its container with SIGSTOP and resumes it with SIGCONT, and records what nginx, the server and both kernels see: nginx access and error logs, the server access log, SYN and RST packets captured in both network namespaces, `nstat` listen counters and `ss -lt` of the server's listening socket.

Each condition is one row of `conditions.tsv`. The `from` column names the condition it is derived from, and a row differs from it in one column. Every run of a condition starts from new containers and stops the server once. The fixed load and timing (rate, duration, when the stop happens) are in `scripts/scenario.sh`.

## Run

`.github/workflows/nginx-accept-queue-overflow.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. Each job runs one condition three times (`./run-all.sh <condition>`), and one more job runs `./run-all.sh somaxconn`, which starts the server without load and records the Send-Q of its listening socket with `net.core.somaxconn` raised on the host and in the container.

Each job puts `results/summary.md` and `results/timing.txt` in the job summary and uploads `results/` as the `nginx-accept-queue-overflow-<condition>` artifact. `results/summary.md` lists, per run, whether the observed settings match the row in `conditions.tsv`, and a run whose settings do not match fails the job with a `RUN INVALID` line.

## Files

- `run-all.sh [condition ...]` builds the tools image and runs the given conditions, or every condition and `somaxconn` when none is given
- `scripts/scenario.sh <condition> <run>` runs one condition once into `results/<condition>/rep-<run>/`
- `scripts/pause.sh` counts nginx's established connections to the server per worker, then stops and resumes the server process
- `scripts/somaxconn.sh` writes `results/somaxconn/somaxconn.txt`
- `scripts/analyze.py` writes `analysis.json` for one run
- `scripts/summarize.py` writes `results/summary.md`

## Pinned versions

The images in `Dockerfile`, `scripts/scenario.sh` and `scripts/somaxconn.sh`, and the vegeta version in `Dockerfile`.
