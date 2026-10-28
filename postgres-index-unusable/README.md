# postgres-index-unusable

This reproduces, on PostgreSQL 18 with the official Docker image's default locale, queries that read the whole table although the column in the condition has an index, as they appear in the slow query log and in `EXPLAIN (ANALYZE)` when the logged statement is run in psql with the logged values. The statements are sent from psycopg 3 with the default cursor, which sends the values apart from the statement, and with `ClientCursor`, which writes the values into the statement. It records the server log, the plans auto_explain writes for the statements psycopg sent, `EXPLAIN (ANALYZE)` of the logged statements and of `PREPARE` with a given parameter type, `pg_prepared_statements`, a pg_dump restored into a database with another default collation, and the DDL Django generates for indexed `CharField`s.

## Run

`.github/workflows/postgres-index-unusable.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. Each `run` job runs `./run-all.sh <part>` for one part and is named after the article section that uses it, followed by the part:

| Job | Part | What it runs |
|---|---|---|
| `2 (log)` | `log` | Statements from psycopg run again in psql with the logged values, and the pg_dump restore |
| `3 (reproduced)` | `reproduced` | `\d members`, the reproduced statements with `enable_seqscan = off`, a `varchar` column compared with a `text` value, `LIKE` with each kind of index on `code`, and the Django DDL |
| `4 (not-reproduced)` | `not-reproduced` | `PREPARE` with the types psycopg sends for `Decimal` and `float`, and one connection running the same statement 11 times |
| `5 (types)` | `types` | `PREPARE` with each pair of column type and parameter type, and the parameter types psycopg sends with `prepare=True` |

Each `run` job writes `results/summary.md` to its job summary and uploads `results/` as the `postgres-index-unusable-<part>` artifact. The `summary` job merges the results of all `run` jobs with `scripts/summary.py` into one `results/summary.md`, grouped by article section with the job name on each result and a section for the results the article does not use, and writes it to its job summary, to a comment on the open pull request for the branch (the same comment is overwritten on every run), and to the `postgres-index-unusable-summary` artifact.

A part is reported as `RUN INVALID` and fails when one of the base queries in `scripts/lab.py` (`BASE`) does not use an index, or when `scripts/django_ddl.py` fails. `scripts/summary.py` also writes a `RUN INVALID` line for each part that has no `results/<part>/data.json` (in a `run` job, for the part that job runs), and the `summary` job then fails.

## Setup

`docker-compose.yml` starts PostgreSQL with `log_min_duration_statement = 0`, so every statement is logged with its parameters, `application_name` in the log line prefix, and auto_explain loaded with `auto_explain.log_min_duration = 0`, so the plan of every statement is logged. The database `lab` is created by the image's entrypoint with the image's default locale.

`schema.sql` creates `members` with the number of rows set in `run-all.sh` and runs `VACUUM ANALYZE`:

| Column | Type | Index |
|---|---|---|
| `id` | `integer` | primary key |
| `fixed_code` | `char(10)` | `members_fixed_code_idx` |
| `code` | `varchar(20)` | `members_code_idx` |
| `email` | `varchar(255)` | `members_email_idx` |
| `amount` | `numeric(12,2)` | `members_amount_idx` |
| `status` | `varchar(16)`: `locked` in every 10000th row, `active` in the rest | `members_status_idx` |
| `payload` | `text` | none |

`run-all.sh` installs `requirements.txt` into a virtual environment, and `scripts/lab.py` connects with psycopg from the host to the published port and runs psql and pg_dump in the container. Every part starts from a new container.

## Conditions

- psycopg statements: each runs on its own connection with its own `application_name` and autocommit, except the 11 executions on one connection in `not-reproduced`. `scripts/lab.py` finds the `execute` or `statement` line, the `Parameters:` line and the auto_explain plan in the server log by `application_name`, replaces each `$n` in the logged statement with the logged value, quotes included, and runs it in psql with `EXPLAIN (ANALYZE)`
- `log`: the default cursor with `id = %s` and `42`, `Decimal("42")`, `42.0`, `Decimal("42.00")`, with `code LIKE %s`, `lower(email) = %s` and `status = %s`, and `ClientCursor` with `id = %s` and `42.0`. `pg_dump -t members` of `lab`, without `--create`, restored with psql into `staging`, created with `LOCALE_PROVIDER libc LOCALE 'C' TEMPLATE template0`, with `pg_stat_user_tables` and the `LIKE` before and after `ANALYZE`
- `reproduced`: `members_code_idx` is dropped, and indexes on `code` with the default collation, `text_pattern_ops`, `COLLATE "C"`, libc `COLLATE "C.utf8"` and builtin `COLLATE "pg_c_utf8"` are created one at a time, each dropped before the next. `scripts/django_ddl.py` defines a model with a `CharField` with `db_index=True`, one with `unique=True`, and one indexed through `Meta.indexes`, and prints the statements `connection.schema_editor(collect_sql=True)` collects for it
- `not-reproduced`: one connection runs `status = %s` 10 times with `'active'` and once with `'locked'`, reading `pg_prepared_statements` after each execution
- `types`: `PREPARE q(<type>) AS ...` and `EXPLAIN (ANALYZE) EXECUTE q(<value>)`, each pair in its own psql session

## Output

`results/<part>/output.txt` holds every psql command with its output and the server log lines of each psycopg statement, `results/<part>/postgres.log` the server log, and `results/<part>/data.json` everything the part recorded, from which `scripts/summary.py` builds `results/summary.md`.

## Pinned versions

The PostgreSQL image in `docker-compose.yml` and the Python packages in `requirements.txt`.
