# postgres-index-unusable

This reproduces, on PostgreSQL 18 with the official Docker image's default locale, queries that read the whole table although the compared column has an index: a parameter of another type than the column, `LIKE 'prefix%'` against indexes of different collations and operator classes, and a column wrapped in a function. For each query it records `EXPLAIN` and `EXPLAIN` with `enable_seqscan = off`. It also sends the same kind of query from psycopg 3 and records the server log, restores a pg_dump into a database with another default collation, and prints the DDL Django generates for indexed `CharField`s.

## Run

`.github/workflows/postgres-index-unusable.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. Each `run` job runs `./run-all.sh <part>` for one part and is named after the article section that uses it, followed by the part:

| Job | Part | What it runs |
|---|---|---|
| `2 (explain)` | `explain` | P1, P5, P6, P7 with the default collation index, P8, P9, P10, and `EXPLAIN (GENERIC_PLAN)` of statements with `$1` |
| `3 (causes)` | `causes` | P2 to P5, P7 with each index on `code` in turn, P12 (Django), P14 (psycopg 3 and the server log), P13 (pg_dump into a database whose default collation is `C`) |

Each `run` job writes `results/summary.md` to its job summary and uploads `results/` as the `postgres-index-unusable-<part>` artifact. The `summary` job merges the results of all `run` jobs with `scripts/summary.py` into one `results/summary.md`, grouped by article section with the job name on each result and a section for the results the article does not use, and writes it to its job summary, to a comment on the open pull request for the branch (the same comment is overwritten on every run), and to the `postgres-index-unusable-summary` artifact.

A part is reported as `RUN INVALID` and fails when one of the base queries does not use an index: the integer column with an integer parameter, the `char(10)` column with a `bpchar` parameter, the `varchar` column with a `text` parameter, the range `code >= 'C000004' AND code < 'C000005'` that the LIKE stands for, and `email` compared with a string. It also fails when psycopg's statement or its parameters are not found in the server log, or when `scripts/django_ddl.py` fails.

## Setup

`docker-compose.yml` starts PostgreSQL with `log_min_duration_statement = 0`, so every statement is logged with its parameters, and `application_name` in the log line prefix. The database `lab` is created by the image's entrypoint with the image's default locale.

`schema.sql` creates `members` with the number of rows set in `run-all.sh` and runs `VACUUM ANALYZE`:

| Column | Type | Index |
|---|---|---|
| `id` | `integer` | primary key |
| `fixed_code` | `char(10)` | `members_fixed_code_idx` |
| `code` | `varchar(20)` | `members_code_idx` |
| `email` | `varchar(255)` | `members_email_idx` |
| `status` | `varchar(16)`, `active` in 4 of every 5 rows | `members_status_idx` |
| `payload` | `text` | none, it makes the table large enough for a parallel scan |

`run-all.sh` installs `requirements.txt` into a virtual environment, and `scripts/lab.py` connects with psycopg from the host to the published port. Every part starts from a new container.

## Conditions

The type of a parameter is set with `PREPARE q(<type>) AS ...` and the value is passed with `EXPLAIN EXECUTE q(<value>)`. `LIKE` and `lower(email)` are written with literals.

- P1: `integer` column with `numeric` and `double precision` parameters
- P2, P3: `integer` column with `bigint` and `varchar` parameters
- P4, P5: `char(10)` column with `varchar` and `text` parameters
- P6: `varchar` column with a `text` parameter
- P7: `code LIKE 'C000004%'`. In `causes`, `members_code_idx` is dropped, and indexes with the default collation, `text_pattern_ops`, `COLLATE "C"`, libc `COLLATE "C.utf8"` and builtin `COLLATE "pg_c_utf8"` are created one at a time, each dropped before the next
- P8: `lower(email) = '...'`
- P9: `status = 'active'`
- P10: P1, P7 and P8 selecting only the indexed column
- P12: `scripts/django_ddl.py` defines a model with a `CharField` with `db_index=True`, one indexed through `Meta.indexes`, and one with `unique=True`, and prints the statements `connection.schema_editor(collect_sql=True)` collects for it
- P13: `pg_dump -t members` of `lab`, without `--create`, restored with psql into `staging`, created with `LOCALE_PROVIDER libc LOCALE 'C'`, then `ANALYZE`
- P14: psycopg 3 runs `SELECT * FROM members WHERE id = %s` with `Decimal("42")` and `42.0`, each on its own connection. The statement and the parameter found in the log are then explained in psql with the value written in place of `$1`, and through `PREPARE` with the type psycopg sent

## Output

`results/<part>/output.txt` holds the plans as they are produced, `results/<part>/postgres.log` the server log, and `results/<part>/data.json` every plan and log line the part recorded, from which `scripts/summary.py` builds `results/summary.md`.

## Pinned versions

The PostgreSQL image in `docker-compose.yml` and the Python packages in `requirements.txt`.
