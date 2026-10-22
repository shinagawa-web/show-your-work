# transient-502-keepalive-reuse

Puts nginx 1.30, with an `upstream` block and its default keepalive pool, in front of two Node.js 24 backends with their default keep-alive timeout, sends a mix of GET and POST requests through it, and records what happens when the backends close pooled connections on their idle timeout. It records the client's view of every request, the nginx access and error logs, what each backend received, and a packet capture of the connections between nginx and the backends.

## Run

`.github/workflows/transient-502-keepalive-reuse.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. Each job runs one condition from `scripts/run.sh`:

| Condition | What changes from `baseline` |
|---|---|
| `baseline` | Steady load with a fixed number of concurrent clients, nothing else |
| `idle-close` | Bursts of concurrent requests on top of the steady load, at intervals around the backends' idle close |
| `idle-close-nginx-shorter` | `idle-close` with `keepalive_timeout` in the `upstream` block shorter than the backends' (`conf/nginx-keepalive-timeout.conf`) |

`results/summary.md` goes to the job summary: the settings and the output of `scripts/analyze.py` (responses per method, nginx error log lines per message, the failed attempts on pooled connections with whether each request reached the backend, the packet sequence and idle time on its connection, and which bursts were followed by failures). `results/` is uploaded as the `transient-502-keepalive-reuse-<condition>` artifact. A run that did not happen as set up (no requests recorded, an incomplete packet capture, missing bursts) fails the job with a `RUN INVALID` line.

## Setup

nginx and both backends run in one container and talk over the loopback interface. The nginx configuration has only `upstream` and `proxy_pass`, plus the access log format and a header that carries `$request_id` (`conf/nginx.conf`). The backend (`backend/server.js`) uses the defaults of `http.createServer`, and records each request when its headers arrive. The load generator (`load/load.js`) puts a unique id in each request's path, so that error log lines, access log lines, backend records and captured requests can be matched.

## Pinned versions

The images in `Dockerfile`.
