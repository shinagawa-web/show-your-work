# rate-limit-cannot-protect

nginx (OSS) `limit_req` with a per-client key, in front of a downstream service that can work on a fixed number of requests at a time. Every scenario starts from the same state and changes one item at 8 s, except the control scenario, which changes nothing. The run records what k6, nginx, the downstream and the kernel saw, and `scripts/analyze.py` checks each scenario against its prediction.

## Setup

```
k6 (constant-arrival-rate) -> nginx 1.30.5 (limit_req, proxy_pass) -> downstream (Go)
```

### Common starting point

- Load (`k6/load.js`): one `constant-arrival-rate` schedule of 600 iterations/s for 25 s in every scenario. Each tenant sends 20 r/s; with N tenants an iteration sends a request when `iterationInTest % (600 / (N * 20)) == 0`. With 10 tenants (t0 to t9) every 3rd iteration sends, i.e. 200 r/s, one request every 5 ms. The tenant of each request is picked at random among the N and sent in `X-Tenant`. All requests go to `/api/light`. Client timeout 60 s.
- nginx (`nginx/base.conf`): `limit_req_zone $http_x_tenant rate=50r/s`, `limit_req burst=20 nodelay`, `limit_req_status 429`; upstream with `zone` and one `server` line; `proxy_read_timeout 1s`; `proxy_set_header X-Request-Id $request_id`. Everything else about proxying is the nginx default (`proxy_http_version`, the `Connection` header, upstream keepalive, `proxy_next_upstream`). `worker_processes auto` and `worker_connections 8192` (with a matching open file limit in `docker-compose.yml`) keep nginx's own connection table out of the way.
- Downstream (`downstream/main.go`): one listener on `:8081` that accepts at most 20 connections at a time (the Accept loop waits on a semaphore before calling accept); connections over the limit wait in the kernel accept queue. `/api/light` takes 50 ms, `/api/heavy` 300 ms. Each response carries `Connection: close` and the connection is closed right after it. The processing is not cancelled when nginx goes away. Settings change at runtime through `GET :9001/admin?limit=&delay_light=&delay_heavy=` (`reset=1` goes back to the startup values).
- Backlog: `net.core.somaxconn=65535` and `net.ipv4.tcp_max_syn_backlog=65535` in the downstream's network namespace (`results/00-environment.txt` records the LISTEN socket's Send-Q).
- Time 0 is the k6 test start (`T0_MS`, logged by `k6/load.js`). The switch is at 8 s: the k6 side changes from that moment, and a second k6 scenario calls `/admin` at 8 s and logs `GET /admin` right before and after (`ADMIN_BEFORE`, `ADMIN_SET`, `ADMIN_AFTER`).

### Scenarios

Control scenario (nothing changed at 8 s):

| scenario | at 8 s |
|---|---|
| 00-control | the switch runs as in the other scenarios with the common values: k6 takes its "after" branch with `TENANTS_AFTER=10` and `HEAVY_AFTER=0`, and the second k6 scenario sends `/admin?limit=20&delay_light=50&delay_heavy=300` |

Cause scenarios (one item changed at 8 s):

| scenario | change at 8 s |
|---|---|
| 01-a-heavy-share | `/api/heavy` share 0% -> 40% (k6 `HEAVY_AFTER=0.4`) |
| 02-b-latency | `/api/light` 50 ms -> 120 ms (`/admin?delay_light=120`) |
| 03-c-capacity | downstream limit 20 -> 8 (`/admin?limit=8`) |
| 04-d-tenants | tenants 10 -> 30, 20 r/s each (k6 `TENANTS_AFTER=30`) |
| 05-b-front-cuts | as 02, with `proxy_read_timeout` at its default (`nginx/read-timeout-default.conf`) and a k6 client timeout of 1 s |

Measure scenarios (a cause scenario plus one item):

| scenario | cause | added |
|---|---|---|
| 06-a-max-conns | A | `max_conns=20` on the server line (`nginx/max-conns.conf`) |
| 07-b-max-conns | B | `max_conns=20` |
| 08-c-max-conns | C | `max_conns=20` |
| 09-d-max-conns | D | `max_conns=20` |
| 10-b-read-timeout-500ms | B | `proxy_read_timeout 500ms` instead of 1s (`nginx/read-timeout-500ms.conf`) |
| 11-b-fixed-key-limit | B | a fixed-key zone `limit_req_zone all zone=fixed rate=160r/s` with `limit_req zone=fixed burst=20 nodelay`, stacked on the per-tenant zone (`nginx/fixed-key-limit.conf`) |

`scenarios/<name>.env` holds only what differs from the common starting point; `scripts/expect.py` holds the predictions and the list of expected differences per scenario.

### Observation

What a reader of the logs has:

- nginx access log: `$msec $request_time $status $uri $http_x_tenant "$upstream_addr" "$upstream_status" "$upstream_connect_time" "$upstream_header_time" "$upstream_response_time" $request_id`
- nginx `error.log` at the default level (`error`); `error-info.log` gets the same at `info`, for the client-abort lines
- downstream access log: start (when the handler got the request), processing time in ms, path, `X-Request-Id`, `X-Tenant`, request protocol, `Connection` header
- `nstat` `TcpExtListenOverflows` / `TcpExtListenDrops` (the counters node_exporter exposes as `node_netstat_TcpExt_*`)

Kernel-side probes, for cross-checking only:

- `sampler/main.go` (`kernel` service, in the downstream's network and PID namespaces), every 100 ms to `kernel_100ms.csv`: Recv-Q (accept queue) and Send-Q (backlog) of the LISTEN socket; `inuse` = `:8081` sockets in ESTABLISHED or CLOSE-WAIT with an owning process (accepted, not yet closed); the nstat counters; the downstream settings from `GET /admin`; `sample_ms` = how long the round took.
- `bpf/accept.bt` (`bpf` service, bpftrace): accept wait from SYN_RECV -> ESTABLISHED to `inet_csk_accept` returning the socket (`A` lines), and accept-to-close and established-to-close on `tcp_close` (`C` lines).

### Run order

The stack starts once (`scripts/run-all.sh`). Per scenario: copy the scenario's nginx config, `nginx -t`, `nginx -s reload`, save `nginx -T`; `/admin?reset=1`; check that the accept queue and the accepted sockets are 0; run k6 with the scenario's env (saved to `k6-env.txt`); then wait, with the scenario's settings still in place, until the downstream has accepted and answered everything nginx sent (accept queue 0, no `:8081` socket left).

`scripts/slice.py` cuts the shared logs per scenario (from just before k6 starts to the end of that wait). `scripts/analyze.py` writes `results/<scenario>/summary.txt` (per-second table) and `results/checks.txt`: for every scenario, each check with its prediction, the observed value and pass / fail, then the error.log / access log lines of each kind. Where a prediction comes without a tolerance, the tolerance used is printed in the prediction column. A host pause check records the largest gap in the k6 schedule and the longest sampler round of each run; when either exceeds 50 ms the scenario is marked `RUN INVALID` in `checks.txt` and is to be rerun, not counted.

## Run

On a Linux host (kernel with BTF, for bpftrace) with Docker, Docker Compose v2 and `python3`. On macOS, `scripts/vm-run.sh` runs the same inside the Lima VM from `lima.yaml` (`limactl create --name=rate-limit-cannot-protect lima.yaml`) and copies `results/` back.

```
./scripts/run-all.sh                    # every scenario
./scripts/run-all.sh 02-b-latency       # a subset
```

Per scenario, `results/<scenario>/` holds `k6.csv`, `k6-summary.json`, `k6-stdout.txt` (T0 and the `/admin` responses), `k6-env.txt`, `scenario.env`, `nginx-T.txt`, `nginx-reload.txt`, `init.txt`, `meta.env`, `access.log`, `error.log`, `error-info.log`, `downstream_access.log`, `kernel_100ms.csv`, `bpf.log` and `summary.txt`. `results/00-base-nginx-T.txt` is `nginx -T` of the common starting point, `results/00-environment.txt` the versions and kernel settings, `results/00-connection-check.txt` a few requests through nginx and the sockets left afterwards, `results/timing.txt` the wall time of each scenario and of the whole run.

## Pinned versions

`grafana/k6:2.3.0`, `nginx:1.30.5`, `golang:1.27.1-bookworm` (build stage in `Dockerfile`; the downstream and the sampler use the Go standard library only), `ubuntu:24.04` with its `iproute2` and `bpftrace` packages for the probes (`00-environment.txt` records the installed versions). The downstream runs from a `scratch` image.
