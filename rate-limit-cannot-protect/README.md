# rate-limit-cannot-protect

nginx (OSS) `limit_req` keeps the entry rate flat and within its limit, while the downstream service behind it gets slower and starts failing. This directory reproduces that with an open-model load and records, per scenario, what the entry, nginx and downstream saw.

## Setup

```
k6 (constant-arrival-rate) -> nginx (limit_req, proxy_pass) -> downstream (Go)
```

The downstream keeps no queue and no metrics of its own. Everything is measured on the nginx side and in the kernel.

- `downstream/main.go`: one listener on `:8081` that accepts at most N connections at a time (default 20; the Accept loop waits on a semaphore before calling accept). Connections over the limit wait in the kernel accept queue. Each request sleeps for the endpoint's processing time (`/api/light` 50 ms, `/api/heavy` 500 ms), answers 200, and the connection is closed at once. It is hijacked from `net/http`, so the 500 ms `rstAvoidanceDelay` does not hold a slot. The sleep is not cancelled when nginx goes away. `:9001/admin?limit=&delay_light=&delay_heavy=&reset=1&drain=` changes the settings at runtime on a separate listener with no limit.
- Backlog: `net.core.somaxconn=65535` and `net.ipv4.tcp_max_syn_backlog=65535` in the downstream container, so the listen backlog (Send-Q of the LISTEN socket) is 65535. `results/00-environment.txt` records it.
- nginx talks to the downstream with `proxy_http_version 1.0` and `Connection: close`, so each upstream request uses its own connection. Without these two settings, nginx 1.30.5 kept upstream connections open, and the idle connections held slots of the limit. `results/00-connection-check.txt` records a few requests and the sockets left afterwards in both network namespaces.
- nginx logs `$msec $request_time $status $uri $http_x_tenant $upstream_addr $upstream_status $upstream_connect_time $upstream_header_time $upstream_response_time`. nginx in-flight requests are counted from these intervals ($msec - $request_time to $msec).
- `sampler/main.go` (`kernel` service, sharing the downstream's network and PID namespaces) writes to `kernel_100ms.csv` every 100 ms:
  - Recv-Q (accept queue length) and Send-Q (backlog) of the LISTEN socket, from `ss -ltn`
  - `inuse`: `:8081` sockets in ESTABLISHED or CLOSE-WAIT that have an owning process in `ss -p`, i.e. accepted and not yet closed. The same sockets without an owner are still in the accept queue.
  - `nstat` TcpExtListenOverflows / TcpExtListenDrops
  - the current settings, from `GET /admin`
- `bpf/accept.bt` (`bpf` service, bpftrace, privileged):
  - accept wait: from `tracepoint:sock:inet_sock_set_state` (SYN_RECV -> ESTABLISHED on port 8081) to `kretprobe:inet_csk_accept` returning that socket
  - accept-to-close time: from the accept to `kprobe:tcp_close` on that socket, i.e. processing time plus the write and the close. The close line also carries the time since ESTABLISHED, which `analyze.py` uses to match each connection to its nginx log line (start and end times) and so to split the accept-to-close time by URI.
  - the count of ESTABLISHED connections, once per second
- `k6/load.js`: 200 r/s by default. A `switch` scenario calls downstream `/admin` at `SWITCH_AT`. The heavy share and the tenant set change on the k6 side. In the tenant scenarios (05, 08*), `TENANT_PICK=random` picks the tenant of each request at random (default); `TENANT_PICK=rr` assigns `iterationInTest % N`, which gives each tenant a fixed phase on the evenly spaced arrival schedule. `ARRIVAL=bernoulli` runs the executor at `THIN` times the rate and sends with probability 1/`THIN` per iteration, so that arrivals are random (approximately Poisson) at the same mean rate.
- `nginx/d-*.conf` (scenario D): the upstream lists the same `downstream:8081` three times, so nginx treats it as a 3-server upstream while every attempt reaches the same listener and the same limit of 20. With a single `server` line nginx would have no next server to retry and would ignore `max_fails`.

Common settings (`nginx/base.conf`): one `limit_req` zone with a fixed key, `rate=200r/s burst=20 nodelay`, `limit_req_status 429`, `proxy_read_timeout 1s`, `proxy_next_upstream off`.

## Scenarios

Each scenario runs 25 s and changes one condition at 8 s. The first 8 s is the reference.

| scenario | nginx config | change at 8 s |
|---|---|---|
| 01-a-cost-skew | base.conf | `/api/heavy` share 10% -> 50% (`/api/heavy` 300 ms) |
| 02-b-latency-inflation (3 runs) | base.conf | `/api/light` 50 ms -> 120 ms (also 100 ms onset check, `onset.txt`) |
| 03-c-capacity-drop | base.conf | concurrency limit 20 -> 8 |
| 04-d-retry-amplification | d-retry.conf (3 servers, `max_fails=0`, `proxy_read_timeout 200ms`, `proxy_next_upstream error timeout`, `proxy_next_upstream_tries 3`) | `/api/light` 50 ms -> 250 ms |
| 04a-d-no-retry-control | d-noretry.conf (d-retry.conf with `proxy_next_upstream off`) | same as 04 |
| 04b-d-retry-nginx-default | d-retry-default.conf (3 servers with no `max_fails` / `fail_timeout`, no `proxy_next_upstream` lines: nginx defaults `max_fails=1 fail_timeout=10s`, `proxy_next_upstream error timeout`, `proxy_next_upstream_tries 0`) | same as 04 |
| 04c-d-retry-default-maxfails0 | d-retry-default-maxfails0.conf (as 04b, with `max_fails=0` on each server) | same as 04 |
| 04d-d-retry-within-capacity | d-retry-short.conf (d-retry.conf with `proxy_read_timeout 60ms`) | `/api/light` 30 ms -> 80 ms. One attempt per request needs 200 x 0.08 = 16 of 20; three need 48 |
| 04e-d-no-retry-within-capacity | d-noretry-short.conf (d-retry-short.conf with `proxy_next_upstream off`) | same as 04d |
| 05-e-tenant-aggregation | e-tenant.conf (per `$http_x_tenant`, 50 r/s, burst 10) | tenants t0-t2 (90 r/s) -> t0-t13 (420 r/s), 30 r/s each on average, tenant picked at random; `/api/light` 60 ms (capacity 20 / 0.06 s = 333 r/s) |
| 06-lever-a-endpoint-limit | f-endpoint.conf (extra 20 r/s zone on `/api/heavy`, burst 10) | same as 01 (`/api/heavy` 300 ms) |
| 07a-lever-b-maxconns-only | g-maxconns.conf (`max_conns=20`, `proxy_read_timeout` 1 s as in base) | same as 02 |
| 07b-lever-b-timeout-only | g-timeout.conf (`proxy_read_timeout 500ms`, no `max_conns`) | same as 02 |
| 08-lever-e-global-limit (3 runs) | h-global.conf (per tenant 50 r/s + fixed key 300 r/s, burst 20) | same as 05 (420 r/s against the 300 r/s zone; tenant at random, evenly spaced arrivals) |
| 08a-lever-e-global-limit-roundrobin (3 runs) | h-global.conf | same as 05, tenant = `iterationInTest % N` |
| 08b-lever-e-global-limit-random-arrival (3 runs) | h-global.conf | same as 05, tenant at random, `ARRIVAL=bernoulli` (`THIN=10`) |

The stack starts once. Before each scenario the runner swaps the nginx config with `nginx -s reload`, sets `/admin?drain=1` (accept everything, answer at once) until the accept queue and the accepted sockets are both 0, calls `/admin?reset=1` with the scenario's initial values, and runs k6. `results/<scenario>/init.txt` records the queue before and after.

## Run

On a Linux host (kernel with BTF, for bpftrace) with Docker, Docker Compose v2 and `python3`. On macOS, `scripts/vm-run.sh` runs the same inside the Lima VM from `lima.yaml` and copies `results/` back.

```
./scripts/run-all.sh                 # every scenario
./scripts/run-all.sh 02-b-latency-inflation   # a subset
```

In CI (`.github/workflows/rate-limit-cannot-protect.yml`) the 24 runs are split into four groups of six runs, one job per group; the matrix in that file lists the scenarios of each group. Each job starts its own stack, runs `scripts/run-all.sh` with its group's scenarios, and fails if the scenario loop in `results/timing.txt` exceeds 300 s. All runs of a scenario with `RUNS=n` stay in one group, so `runs.txt` compares them. Each job writes `timing.txt`, `00-environment.txt`, the `runs.txt` files and the `summary.txt` files to the job summary and uploads `results/` as the artifact `rate-limit-cannot-protect-results-<group>`.

Output goes to `results/<scenario>/` (for a scenario with `RUNS=n`, to `results/<scenario>/run-<i>/`, plus `results/<scenario>/runs.txt` with the runs side by side): `summary.txt` (per-second table and windows [0,8), [8,25), [16,25)), `onset.txt` for 02 (100 ms ordering), `k6.csv`, `access.log`, `kernel_100ms.csv`, `bpf.log`, `init.txt`, `error.log` (nginx, second resolution), the env and nginx config used. `results/timing.txt` has the wall time of each run and of the scenario loop, `results/00-environment.txt` the versions.

## Pinned versions

`grafana/k6:2.3.0`, `nginx:1.30.5`, `golang:1.27.1-bookworm` (build stage in `Dockerfile`; the downstream and the sampler use the Go standard library only), `ubuntu:24.04` with its `iproute2` and `bpftrace` packages for the probes (`00-environment.txt` records the installed versions). The downstream runs from a `scratch` image.
