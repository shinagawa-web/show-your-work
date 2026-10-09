# select-for-update-throughput-ceiling

This measures the throughput of transactions that take a row lock with `SELECT ... FOR UPDATE` on one stock row in PostgreSQL 16, and compares it with a conditional `UPDATE ... WHERE stock > 0`.

## What it runs

`run.py` starts N threads, each with its own connection, and lets every thread loop over one transaction for a 30s window. After the window each thread finishes the transaction it is in and stops. TPS is the number of commits divided by the time from the start until the last thread stops.

- `for_update`: `SELECT stock ... FOR UPDATE`, `pg_sleep(hold)`, `UPDATE items SET stock = stock - 1`, `INSERT INTO orders`, `COMMIT`
- `conditional`: `UPDATE items SET stock = stock - 1 WHERE id = 1 AND stock > 0`, `INSERT INTO orders`, `COMMIT`

With `--observe`, it also counts sessions in `pg_stat_activity` with `wait_event_type = 'Lock'` every 0.5s.

`run_all.sh` runs three rounds, each condition twice:

| Round | Pattern | Concurrency | hold | Initial stock |
|---|---|---|---|---|
| 1 | `for_update` | 20 | 0.1s, 1.0s, 5.0s | 1000 |
| 2 | `for_update` with `--observe` | 1, 5, 10, 20, 50 | 0.1s | 1000 |
| 3 | `for_update` and `conditional` | 20 | 0.1s (`for_update` only) | 1000 and 200000 |

The schema is in `schema.sql`. PostgreSQL runs with `max_connections=200` (`docker-compose.yml`).

## Run it

On a host with Docker:

```
cd select-for-update-throughput-ceiling
./run_all.sh
```

## Results

The CI job summary has the output of every round, without the per-sample lock waiter lines. The artifact carries the full output.
