# select-for-update-throughput-ceiling

Runs PostgreSQL 18 with its default settings behind a Python application server that takes one stock row with `SELECT ... FOR UPDATE`, does its own work while holding the lock, and commits. Requests arrive at random intervals (Poisson arrivals) and are not limited in how many are in flight. It records every request from its scheduled arrival, the OpenTelemetry span of every SQL statement, and the CPU use of the PostgreSQL container.

## Run

`.github/workflows/select-for-update-throughput-ceiling.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. Each job runs one condition from `conditions.tsv`:

| Condition | What changes from `baseline` |
|---|---|
| `baseline` | Nothing |
| `p1` | A higher arrival rate |
| `p2` | `p1` with a larger connection pool |
| `p3` | `p1` with longer work inside the transaction |
| `p4` | `p1` with a second request type, one `SELECT` without a lock, on the same pool |
| `f1` | `p1` with the work moved before the transaction starts |

`results/summary.md` goes to the job summary: the settings, the PostgreSQL version and `max_connections`, and the output of `scripts/analyze.py`. `results/` is uploaded as the `select-for-update-throughput-ceiling-<condition>` artifact. A run that did not happen as set up (requests that started late against their schedule, errors other than the pool timeout, missing spans, orders in the database that differ from the completed requests, stock running out, no CPU samples) fails the job with a `RUN INVALID` line.

## Setup

The application server (`app/server.py`) has one `psycopg_pool` connection pool, with the pool's default timeout for waiting on a connection. It draws the arrival times from a fixed seed, so conditions with the same rate get the same arrivals, and starts each request in its own thread at its scheduled time, without waiting for earlier requests. An order request does:

```sql
SELECT stock FROM items WHERE id = 1 FOR UPDATE;
-- work in the application server (time.sleep), nothing sent to the database
UPDATE items SET stock = stock - 1 WHERE id = 1;
INSERT INTO orders (item_id) VALUES (1);
-- conn.commit()
```

psycopg sends `BEGIN` before the first statement. In `f1` the work runs after the connection is taken from the pool and before the first statement.

The OpenTelemetry psycopg instrumentation writes a span for each statement run through a cursor to `results/<condition>/spans.jsonl`, under a root span that starts before the connection is taken from the pool. It does not make a span for `conn.commit()`, so `results/<condition>/requests.jsonl` records the time `conn.commit()` returned, along with the scheduled arrival, the root span start and the time the connection was taken. `scripts/cpu_sample.py` reads the `cpu.stat` of the PostgreSQL container's cgroup from the host into `results/<condition>/cpu.tsv`.

`scripts/analyze.py` counts COMMITs per second over the arrival period, response times from the scheduled arrival with the requests that hit the pool timeout counted apart, the connection wait and response time per arrival second, the duration of each SQL span, the time from the end of the `SELECT ... FOR UPDATE` span to the end of the last SQL span and to the return of `conn.commit()`, and how many `SELECT ... FOR UPDATE` spans ended after one that started later.

## Pinned versions

The images in `docker-compose.yml` and `app/Dockerfile`, and the Python packages in `app/requirements.txt`.

- psycopg_pool waits up to 30 seconds for a connection by default, with no limit on the number of waiters: https://github.com/psycopg/psycopg/blob/pool-3.3.3/psycopg_pool/psycopg_pool/pool.py#L49-L61
- The psycopg instrumentation traces `execute`, `executemany` and `callproc` on cursors: https://github.com/open-telemetry/opentelemetry-python-contrib/blob/v0.66b1/instrumentation/opentelemetry-instrumentation-psycopg/src/opentelemetry/instrumentation/psycopg/__init__.py#L385-L395
