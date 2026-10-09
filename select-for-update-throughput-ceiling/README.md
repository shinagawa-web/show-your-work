# select-for-update-throughput-ceiling

This measures the throughput of transactions that take a row lock with `SELECT ... FOR UPDATE` on one stock row in PostgreSQL, and compares it with a conditional `UPDATE ... WHERE stock > 0`.

## Run

`.github/workflows/select-for-update-throughput-ceiling.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. Each condition in `run-all.sh` runs as its own job. `results/summary.txt`, the output without the per-sample lock waiter lines, goes to the job summary, and `results/` is uploaded as the `select-for-update-throughput-ceiling-results-<condition>` artifact.

## Output

`run.py` starts one thread per connection and lets every thread loop over one transaction for the measurement window. After the window each thread finishes the transaction it is in and stops.

- `for_update`: `SELECT stock ... FOR UPDATE`, `pg_sleep(hold)`, `UPDATE items SET stock = stock - 1`, `INSERT INTO orders`, `COMMIT`
- `conditional`: `UPDATE items SET stock = stock - 1 WHERE id = 1 AND stock > 0`, `INSERT INTO orders`, `COMMIT`

`count` is the number of commits, `elapsed_s` the time from the start until the last thread stops, and `tps` is `count / elapsed_s`. `p50_ms` and `p99_ms` are the latencies of the committed transactions. With `--observe`, each `lock_waiters` line is the number of active sessions in `pg_stat_activity` with `wait_event_type = 'Lock'` at one sample, with the host CPU use.

## Pinned versions

The images in `Dockerfile` and `docker-compose.yml`.
