# docker-init-cuts-requests

Stops an Express server in the official `node` image with `docker stop` (default grace period of 10 s) while a request is in progress, with and without `docker run --init`, with and without a SIGTERM handler that calls `server.close()`, and with the server started by `CMD` in exec form, in shell form, and from a shell script with and without `exec`. It records how long `docker stop` took, the exit code of the container, whether the request in progress got its response, the process tree in the container, the container log, and the signals the kernel generated for the processes of the container.

## Run

`.github/workflows/docker-init-cuts-requests.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

`./run-all.sh [condition ...]` runs the conditions in `conditions.tsv` (all of them when none is given), three runs each, four at a time. `results/summary.md` goes to the job summary, and `results/` is uploaded as the `docker-init-cuts-requests-results` artifact. The run fails with a `RUN INVALID` line at the end of the summary when a container did not start, the server did not log that it accepted the request before `docker stop`, a condition has no runs, or the request was returned in some runs of a condition and cut in others. In that case the summary lists each run of that condition.

## Setup

`app/server.js` is an Express server on port 3000 with `GET /slow?seconds=<n>`, which logs that it accepted the request and responds after `n` seconds. With `SIGTERM_HANDLER=1` it registers `process.on('SIGTERM')`, which logs, calls `server.close()`, logs again when the server has closed and calls `process.exit(0)`.

`app/Dockerfile` builds one image per way of starting the server:

| Target | `CMD` |
|---|---|
| `exec-form` | `["node", "server.js"]` |
| `shell-form` | `node server.js` |
| `script` | `["./start.sh"]`, which echoes a line and runs `node server.js` |
| `script-exec` | `["./start-exec.sh"]`, which echoes a line and runs `exec node server.js` |

Each run:

1. Starts the container with `docker run -d -p 127.0.0.1:<port>:3000`, `--init` when the condition sets it
2. Waits for the `listening` line, then records `docker top`, `ps` in the container, and the signal masks of each process in the container (`scripts/proc-signals.sh`)
3. Sends `GET /slow` with curl and waits for the line that the server accepted it
4. Runs `docker stop` (default grace period of 10 s), records the signal masks again 1 s after it starts, and waits for curl to return
5. Records `docker inspect`, the container log and the Docker daemon's journal lines for the container

While the runs go on, `run-all.sh` traces the `signal:signal_generate` kernel event for SIGTERM and SIGKILL in its own tracefs instance.

## Conditions

Each row of `conditions.tsv` sets the image target, `--init`, `SIGTERM_HANDLER` and the seconds of the request.

## Outputs

`results/<condition>/run<N>/` holds `docker-top.txt`, `ps-in-container.txt`, `proc-signals-before.txt` and `proc-signals-after-1s.txt`, `stop-seconds.txt` (the time `docker stop` took), `exit-code.txt`, `inspect.json`, `app.log` (`docker logs -t`), `curl-exit.txt`, `curl-stderr.txt`, `curl-stdout.txt` and `curl-body.txt`, `dockerd.log` (the Docker daemon's journal lines for the container) and `signals.txt` (the lines of `results/signal-trace.txt` for the processes in `docker-top.txt`). `results/environment.txt` holds the versions, and `results/timing.txt` the seconds taken.

## Pinned versions

The `node` image in `app/Dockerfile`, and Express in `app/package.json` and `app/package-lock.json`. `results/environment.txt` records the image digest and the versions of Docker, `docker-init` and Node.js.

- Node.js registers its own SIGTERM handler: https://github.com/nodejs/node/blob/v26.13.1/src/node.cc#L625-L628
- The handler resets the terminal and raises the signal again: https://github.com/nodejs/node/blob/v26.13.1/src/node.cc#L190-L193
- It is registered with `SA_RESETHAND`: https://github.com/nodejs/node/blob/v26.13.1/src/node.cc#L487
