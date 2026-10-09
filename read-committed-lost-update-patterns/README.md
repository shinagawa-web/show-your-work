# read-committed-lost-update-patterns

This reproduces, under PostgreSQL 16's default Read Committed isolation, the two ways stock goes wrong when concurrent transactions read a stock row and then update it, and the guards against each.

## What it runs

`run.py` starts N threads at once. Each thread opens its own connection, runs one order in one transaction (`BEGIN ISOLATION LEVEL READ COMMITTED`), and the run ends when all threads have finished. With `--wait`, a thread sleeps between its `SELECT` and its `UPDATE`.

| Pattern | Transaction |
|---|---|
| `A` | `SELECT stock`, sleep, `UPDATE ... SET stock = stock - 1`, `INSERT INTO orders` |
| `B` | `SELECT stock` into `v`, sleep, `UPDATE ... SET stock = v - 1`, `INSERT INTO orders` |
| `C` | `UPDATE ... SET stock = stock - 1 WHERE ... AND stock > 0`, `INSERT INTO orders` if a row was updated |
| `CB` | as `B`, with `AND stock > 0` on the `UPDATE` |
| `D` | as `B`, with `AND stock = v` on the `UPDATE`; retries from the `SELECT` when no row was updated |

A thread that reads `stock <= 0` stops without ordering.

After each run, the measuring query in `run.py` reports the stock left (`stock_sum`), how much was actually subtracted (`decremented`), the orders written (`orders_ok`), their difference (`lost_decrements`), rows below zero (`negative_rows`) and, for `D`, the retries.

`run_all.sh` runs each pattern with 1 thread and with 50 threads on one row (initial stock 20, 0.1s wait), and `A` and `B` with 50 threads spread over 1000 rows (initial stock 5).

## Run it

On a host with Docker:

```
cd read-committed-lost-update-patterns
./run_all.sh
```

## Results

The CI job summary has the table for every run. The artifact carries the full output.
