# read-committed-lost-update-patterns

This reproduces, under PostgreSQL's default Read Committed isolation, the two ways stock goes wrong when concurrent transactions read a stock row and then update it, and the guards against each.

## Run

`.github/workflows/read-committed-lost-update-patterns.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. It runs every condition in `run-all.sh`. `results/summary.txt`, the table of every run, goes to the job summary, and `results/` is uploaded as the `read-committed-lost-update-patterns-results` artifact.

## Output

`run.py` starts the threads at once. Each thread opens its own connection and runs one order in one transaction (`BEGIN ISOLATION LEVEL READ COMMITTED`). With `--wait`, a thread sleeps between its `SELECT` and its `UPDATE`. A thread that reads `stock <= 0` stops without ordering.

| Pattern | Transaction |
|---|---|
| `A` | `SELECT stock`, sleep, `UPDATE ... SET stock = stock - 1`, `INSERT INTO orders` |
| `B` | `SELECT stock` into `v`, sleep, `UPDATE ... SET stock = v - 1`, `INSERT INTO orders` |
| `C` | as `A`, with `AND stock > 0` on the `UPDATE`; `INSERT INTO orders` only if a row was updated |
| `CB` | as `B`, with `AND stock > 0` on the `UPDATE`; `INSERT INTO orders` only if a row was updated |
| `D` | as `B`, with `AND stock = v` on the `UPDATE`; retries from the `SELECT` when no row was updated |

Each row of the table is one run: `stock_sum` is the stock left, `decremented` how much was actually subtracted, `orders_ok` the orders written, `lost_decrements` their difference (`orders_ok - decremented`), `negative_rows` the rows below zero, `min_stock` the lowest stock, and `retries` the retries of `D`.

## Pinned versions

The images in `Dockerfile` and `docker-compose.yml`.
