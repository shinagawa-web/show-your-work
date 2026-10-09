# provisional-hold-inventory-stockout

This measures how the length of the hold timer affects confirmed sales, holds that expire during checkout, and lost sales under finite stock. `sim.py` simulates it in memory, and `run.py` runs the same scenario against PostgreSQL. Both take the same parameters, defined in their argparse options.

## Run

`.github/workflows/provisional-hold-inventory-stockout.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

| Job | What it runs |
|---|---|
| `sim` | `./run-all.sh sim`: `sim.py` with the stock values in `run-all.sh` |
| `run (<n>)` | `./run-all.sh run`: `run.py` for `RUNS` rounds, one job per shard |
| `report` | `report.py` averages the tables of the `run` jobs into one table |

`results/summary.txt` of `sim` and `report` goes to the job summary. Each job uploads its `results/` as an artifact named `provisional-hold-inventory-stockout-results-<job>`.

## Output

Each table has one row per hold timer `T`:

- `confirmed`: customers who completed checkout before their hold expired
- `exp_during_co`: customers whose hold expired while they were checking out
- `lost_sale`: buyers who found no stock while an abandoned hold existed
- `dead_ratio`: mean fraction of reserved holds that belong to abandoners, sampled during the arrival window

`sim.py` counts time in minutes. `run.py` counts it in seconds, one second standing for one minute, and runs every `T` in parallel with its own `product_id`. `sim.py` seeds each run with its index, so it prints the same tables every time; `run.py` does not fix a seed. `report.py` needs every `run` job to have the same number of rounds. `errors` after a `run.py` table counts the database calls that raised; a run with errors is reported as `RUN INVALID` and fails the job, and so does `report` when a shard was invalid.

## Pinned versions

The images in `Dockerfile` and `docker-compose.yml`.
