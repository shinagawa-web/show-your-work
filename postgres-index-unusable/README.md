# postgres-index-unusable

This reproduces, on PostgreSQL 18 with the official Docker image's default locale, queries that read the whole table although the column in the condition has an index: an integer column compared with a numeric value, `LIKE` with a prefix pattern, and a column wrapped in `lower()`. It records `EXPLAIN (ANALYZE)` of each query with `enable_seqscan` on and off, and with each kind of index that is meant to make it use one. The integer column compared with a value is also sent from psycopg 3, with `ClientCursor`, which writes the value into the statement, and with the default cursor, which sends it apart from it, and the statement found in the server log is run again in psql with the logged value.

## Run

`.github/workflows/postgres-index-unusable.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. Each `run` job runs `./run-all.sh <part>` for one part and is named after the article section that uses it, followed by the part:

| Job | Part | What it runs |
|---|---|---|
| `2 (explain)` | `explain` | The three queries with `enable_seqscan` on and off, and the `varchar` column compared with a `text` value |
| `3 (causes)` | `causes` | `PREPARE` with a `bigint` parameter, `LIKE` with each kind of index on `code`, and `lower(email)` with an index on the expression |
| `4 (log)` | `log` | Statements sent from psycopg, run again in psql as they were logged, the plan auto_explain logged, the parameter types psycopg sends with `prepare=True`, and `PREPARE` with the type psycopg sent |

Each `run` job writes `results/summary.md` to its job summary and uploads `results/` as the `postgres-index-unusable-<part>` artifact. The `summary` job merges the results of all `run` jobs with `scripts/summary.py` into one `results/summary.md`, grouped by article section with the job name on each result and a section for the results the article does not use, and writes it to its job summary, to a comment on the open pull request for the branch (the same comment is overwritten on every run), and to the `postgres-index-unusable-summary` artifact.

A part is reported as `RUN INVALID` and fails when one of the base queries in `scripts/lab.py` (`BASE`) does not use an index, or when a statement sent from psycopg is not found in the server log. `scripts/summary.py` also writes a `RUN INVALID` line for each part that has no `results/<part>/data.json` (in a `run` job, for the part that job runs), and the `summary` job then fails.

## Setup

`docker-compose.yml` starts PostgreSQL with `log_min_duration_statement = 0`, so every statement is logged with its parameters, `application_name` in the log line prefix, and auto_explain loaded with `auto_explain.log_min_duration = 0`, so the plan of every statement is logged. The database `lab` is created by the image's entrypoint with the image's default locale.

`schema.sql` creates `members` with the number of rows set in `run-all.sh` and runs `VACUUM ANALYZE`:

| Column | Type | Index |
|---|---|---|
| `id` | `integer` | primary key |
| `code` | `varchar(20)` | `members_code_idx` |
| `email` | `varchar(255)` | `members_email_idx` |
| `payload` | `text` | none |

`run-all.sh` installs `requirements.txt` into a virtual environment, and `scripts/lab.py` connects with psycopg from the host to the published port and runs psql in the container. Every part starts from a new container.

## Conditions

- `explain`: `id = 42.0`, `code LIKE 'C000004%'` and `lower(email) = 'user42@example.com'`, each with the default `enable_seqscan` and after `SET enable_seqscan = off`. After `SET enable_seqscan = off`, `code = 'C0000042'::text`
- `causes`: `PREPARE q(bigint)` of `id = $1` with `EXPLAIN (ANALYZE) EXECUTE q(42)` in one psql session. `members_code_idx` is dropped, and indexes on `code` with the default collation, `text_pattern_ops` and `COLLATE "C"` are created one at a time for `code LIKE 'C000004%'`, each dropped before the next. Then an index on `lower(email)` is created for `lower(email) = 'user42@example.com'`
- `log`: each statement runs on its own connection with its own `application_name` and autocommit. `ClientCursor` with `id = %s` and `42.0`. The default cursor with `id = %s` and `Decimal("42")` and `Decimal("42.00")`. `scripts/lab.py` finds the `execute` or `statement` line, the `Parameters:` line and the auto_explain plan in the server log by `application_name`, replaces each `$n` in the logged statement with the logged value, quotes included, and runs it in psql with `EXPLAIN (ANALYZE)`. The default cursor also sends `Decimal("42")` with `prepare=True` and reads `pg_prepared_statements`, and psql runs `PREPARE q(numeric)` of `id = $1` with `EXECUTE q('42')`

## Output

`results/<part>/output.txt` holds every psql command with its output and the server log lines of each psycopg statement, `results/<part>/postgres.log` the server log, and `results/<part>/data.json` everything the part recorded, from which `scripts/summary.py` builds `results/summary.md`.

## Pinned versions

The PostgreSQL image in `docker-compose.yml` and the Python packages in `requirements.txt`.
