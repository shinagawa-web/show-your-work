# mysql-index-unusable

This reproduces, on MySQL 8.4 and 8.0, queries that read the whole table although the compared column has an index: a VARCHAR column compared with a number, columns of different character sets or types compared in a join, and a column wrapped in a function. For each query it records `EXPLAIN`, the `SHOW WARNINGS` that follows it in the same session, `EXPLAIN` with `FORCE INDEX`, and the slow query log entry of the query when it is executed. It also runs the same kinds of queries from Laravel's `illuminate/database`, restores a mysqldump into a second server to compare `EXPLAIN` there, and finally converts the utf8mb3 join column to utf8mb4 and runs the join again.

## Run

`.github/workflows/mysql-index-unusable.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. It has two jobs that run in parallel:

- `mysql-8.4` runs `./run-all.sh 8.4`: every condition below
- `mysql-8.0` runs `./run-all.sh 8.0`: every condition below except the join with `b_mb4_0900` and three single-table conditions: the INT column compared with a string, the queries that select only the indexed column, and the `b_mb3_unicode` column compared with a string

Each job writes `results/summary.txt` to the job summary and uploads `results/` as the `mysql-index-unusable-results-8.4` or `mysql-index-unusable-results-8.0` artifact.

## Setup

`docker-compose.yml` starts MySQL with the slow query log written to a file and `long_query_time = 0`, so every statement is logged. It also starts a second server, `restore`, with the same image, and builds the PHP image in `Dockerfile`.

`schema.sql` creates the tables with the number of rows set in `run-all.sh`, then runs `ANALYZE TABLE`:

| Table | Charset and collation | Used for |
|---|---|---|
| `members` | utf8mb4, utf8mb4_unicode_ci | single-table conditions, and table `a` of every join |
| `stores` | utf8mb4, utf8mb4_unicode_ci | parent of `members.store_id` in the Laravel `hasMany` condition |
| `b_mb4_unicode` | utf8mb4, utf8mb4_unicode_ci | table `b` of the base join |
| `b_mb3_unicode` | utf8mb3, utf8mb3_unicode_ci | table `b` with another character set |
| `b_mb4_0900` | utf8mb4, utf8mb4_0900_ai_ci | table `b` with another collation of the same character set |
| `b_int` | utf8mb4, utf8mb4_unicode_ci, `member_code` is INT | table `b` with another column type |

Every `b` table has one row per member, with `member_code` holding `members.code`, and the same indexed `batch_id`, which groups the rows like `members.account_id`. The client connects with `--default-character-set=utf8mb4`, and Laravel with `utf8mb4` and `utf8mb4_unicode_ci`.

## Conditions

`run-all.sh` prints each condition under a `===` heading.

- `single:` queries on `members`, and one on `b_mb3_unicode`, starting from `code = '1000042'`. Two of them check fixes for the function on a column: the same date written as a range on `created_at`, and a functional index on `DATE(created_at)`, which is added just before its query and dropped right after
- `join:` `members a` joined with one `b` table on `b.member_code = a.code`, filtered by `a.account_id`. Each is explained without a hint, with `STRAIGHT_JOIN` from `a`, and with `STRAIGHT_JOIN` from `b`, then executed the same three ways. The join with `b_int` is also explained and executed filtered by `b.batch_id` instead of `a.account_id`
- `laravel:` `laravel.php` runs one step per invocation: `where('code', '1000042')`, `where('code', 1000042)`, `Store::find(42)->members`, `whereDate`, and the `CREATE TABLE` the schema builder generates for two connection settings (`pretend`, not executed). The statement found in the slow log is then explained as written there
- `staging:` changes the table default of `b_mb3_unicode` to utf8mb4 without converting its columns, dumps `members` and `b_mb3_unicode` with mysqldump, restores the dump into `restore`, and explains the same queries on both servers, and on `restore` again after `ANALYZE TABLE`
- `fix:` after every other condition, on the source server, converts `b_mb3_unicode.member_code` to utf8mb4 and utf8mb4_unicode_ci with `ALTER TABLE ... MODIFY`, then explains and executes the unhinted join of `members a` with `b_mb3_unicode b`. The other columns of `b_mb3_unicode` stay utf8mb3

## Output

`results/output.txt` holds everything the run prints. For each executed query, it shows the slow log entries written while the query ran, leaving out the health check, the client's `select @@version_comment` and the `Quit` entries. `results/summary.txt` is the same without the client's separator lines. `results/slow-<step>.log` holds the slow log entries of each Laravel step, and `results/dump-create-tables.sql` the `CREATE TABLE` statements of the dump.

The run is reported as `RUN INVALID` and fails when the base single-table query does not use `idx_code`, when the base join does not read `b` through `idx_member_code`, or when a Laravel step leaves no `select` in the slow log.

## Pinned versions

The MySQL images in `run-all.sh`, the images in `Dockerfile`, and the Laravel packages in `composer.json`.
