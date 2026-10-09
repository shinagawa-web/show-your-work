# provisional-hold-inventory-stockout

Simulations for measuring how hold timer length affects confirmed sales, checkout expirations, and lost sales under finite stock.

Two scripts share the same parameters and produce the same output format. `sim.py` runs without a database; `run.py` runs against PostgreSQL.

## sim.py — in-memory simulation

No database required. Runs fast; useful for sweeping parameter space.

```
python sim.py [options]
```

| Option | Default | Description |
|---|---|---|
| `--stock` | 100 | Initial inventory |
| `--customers` | 300 | Number of arrivals |
| `--window` | 30.0 | Arrival window in minutes |
| `--purchase` | 2.0 | p50 checkout duration (minutes) |
| `--p95` | 3× purchase | p95 checkout duration (minutes) |
| `--abandon-rate` | 0.7 | Fraction of customers who abandon |
| `--timers` | `1,2,3,5,7,10,15,20` | Hold timer values to sweep (minutes) |
| `--sweep` | 10s | Expiry sweep interval (minutes) |
| `--remove-rate` | 0.0 | Fraction of abandoners who actively remove their hold |
| `--runs` | 30 | Seeds to average over |

Example:

```
python sim.py --customers 500 --stock 150
```

## run.py — PostgreSQL simulation

Runs the same scenario against a real database. Each timer value gets its own `product_id`; all run in parallel.

Start the database and create the schema first:

```
docker compose up -d --wait
docker compose exec -T postgres psql -U postgres -d lab -f /dev/stdin < schema.sql
```

Then:

```
python run.py [options]
```

| Option | Default | Description |
|---|---|---|
| `--stock` | 100 | Initial inventory per product |
| `--customers` | 300 | Number of arrivals |
| `--window` | 30.0 | Arrival window in seconds (1 s here = 1 min real) |
| `--purchase` | 2.0 | p50 checkout duration (seconds) |
| `--p95` | 3× purchase | p95 checkout duration (seconds) |
| `--abandon-rate` | 0.7 | Fraction of customers who abandon |
| `--timers` | `1,2,3,5,7,10,15,20` | Hold timer values to sweep (seconds) |
| `--runs` | 30 | Rounds to average over |

Database connection is read from environment variables: `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`. Defaults point to the `docker-compose.yml` instance.

Example:

```
python run.py --customers 500 --stock 150
```

## Output

Both scripts print a table with one row per timer value:

```
T(min)   confirmed  exp_during_co  lost_sale dead_ratio
     1      ...            ...        ...        ...
     2      ...            ...        ...        ...
```

- `confirmed` — customers who completed checkout before their hold expired
- `exp_during_co` — customers whose hold expired while they were checking out
- `lost_sale` — buyers who found no stock but an abandoned hold existed
- `dead_ratio` — mean fraction of reserved holds that belong to abandoners, sampled during the arrival window

## CI

`run_all.sh` runs `sim.py` with 30 seeds, with the defaults above and again with `--stock` 150, 200 and 300, and `run.py` with 3 rounds and the defaults. `sim.py` seeds each run with its index, so it prints the same table every time. `run.py` does not fix a seed, so its table changes from run to run. The CI job summary has both tables, and the artifact carries the full output.

`run.py` releases expired holds with one sweeper that runs every 0.5s (30s of simulated time) and processes up to 100 holds per run, oldest first, with `FOR UPDATE SKIP LOCKED`. `sim.py` releases them every 10s of simulated time (`--sweep`).
