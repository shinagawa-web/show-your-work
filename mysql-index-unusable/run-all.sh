#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

version=${1:-8.4}
case "$version" in
  8.4) export MYSQL_IMAGE=mysql:8.4.11 ;;
  8.0) export MYSQL_IMAGE=mysql:8.0.46 ;;
  *) echo "usage: ./run-all.sh [8.4|8.0]" >&2; exit 2 ;;
esac
rows=300000
slow_log=/var/lib/mysql/slow.log

heading() { printf '\n=== %s ===\n' "$*"; }

session() {
  local server=${2:-mysql}
  docker compose exec -T "$server" mysql -uroot --default-character-set=utf8mb4 --table -vvv --force lab <<<"$1" || true
}

explain() {
  session "EXPLAIN $1; SHOW WARNINGS;" "${2:-mysql}"
}

slow_size() { docker compose exec -T mysql stat -c %s "$slow_log"; }

slow_since() {
  docker compose exec -T mysql tail -c +"$(( $1 + 1 ))" "$slow_log" | awk '
    function flush() { if (entry != "" && entry !~ /@ +\[127\.0\.0\.1\]|@@version_comment|administrator command: Quit/) printf "%s", entry }
    /^# Time: / { flush(); entry = "" }
    { entry = entry $0 "\n" }
    END { flush() }'
}

execute() {
  local mark
  mark=$(slow_size)
  echo "query: $1"
  docker compose exec -T mysql mysql -uroot --default-character-set=utf8mb4 lab -e "$1" > /dev/null || true
  echo "slow log:"
  slow_since "$mark"
}

laravel() {
  local mark
  mark=$(slow_size)
  docker compose run --rm -T php php laravel.php "$1"
  echo "slow log:"
  slow_since "$mark" | tee results/slow-$1.log
}

logged_select() { grep -E '^select ' "results/slow-$1.log" | tail -n 1; }

invalid() { echo "RUN INVALID: $*"; exit 1; }

join_sql() { echo "SELECT a.code, b.payload FROM $1 WHERE a.account_id = 42"; }

setup() {
  heading "setup mysql $MYSQL_IMAGE rows=$rows"
  docker compose --profile php down -v > /dev/null 2>&1
  local services=(mysql)
  [ "$version" = 8.4 ] && services+=(restore)
  docker compose up -d --wait --quiet-pull "${services[@]}"
  [ "$version" = 8.4 ] && docker compose --profile php build --quiet php
  local start=$SECONDS
  { echo "SET @rows = $rows;"; cat schema.sql; } | docker compose exec -T mysql mysql -uroot --default-character-set=utf8mb4 lab
  echo "load seconds: $(( SECONDS - start ))"
  session "SELECT VERSION(), @@character_set_connection, @@collation_connection;
SELECT COUNT(*) FROM members; SELECT COUNT(*) FROM b_mb3_unicode; SELECT COUNT(*) FROM b_int;
SHOW CREATE TABLE members\G
SHOW CREATE TABLE b_mb4_unicode\G
SHOW CREATE TABLE b_mb3_unicode\G
SHOW CREATE TABLE b_mb4_0900\G
SHOW CREATE TABLE b_int\G
SHOW INDEX FROM members;"
}

single() {
  heading "single: varchar column = string (base)"
  local base_explain
  base_explain=$(explain "SELECT * FROM members WHERE code = '1000042'")
  echo "$base_explain"
  grep -qE '\| ref +\| idx_code ' <<<"$base_explain" || invalid "base query does not use idx_code"
  execute "SELECT * FROM members WHERE code = '1000042'"

  heading "single: varchar column = number"
  explain "SELECT * FROM members WHERE code = 1000042"
  explain "SELECT * FROM members FORCE INDEX (idx_code) WHERE code = 1000042"
  execute "SELECT * FROM members WHERE code = 1000042"

  heading "single: function on column"
  explain "SELECT * FROM members WHERE DATE(created_at) = '2010-01-01'"
  execute "SELECT * FROM members WHERE DATE(created_at) = '2010-01-01'"
  explain "SELECT * FROM members WHERE LOWER(email) = 'user42@example.com'"
  execute "SELECT * FROM members WHERE LOWER(email) = 'user42@example.com'"

  [ "$version" = 8.4 ] || return 0

  heading "single: integer column = string"
  explain "SELECT * FROM members WHERE account_id = '42'"
  execute "SELECT * FROM members WHERE account_id = '42'"

  heading "single: only indexed column selected"
  explain "SELECT code FROM members WHERE code = 1000042"
  execute "SELECT code FROM members WHERE code = 1000042"
  explain "SELECT created_at FROM members WHERE DATE(created_at) = '2010-01-01'"
  execute "SELECT created_at FROM members WHERE DATE(created_at) = '2010-01-01'"

  heading "single: utf8mb3 column = string over utf8mb4 connection"
  session "SELECT @@character_set_connection, @@collation_connection;
EXPLAIN SELECT * FROM b_mb3_unicode WHERE member_code = '1000042'; SHOW WARNINGS;"
  execute "SELECT * FROM b_mb3_unicode WHERE member_code = '1000042'"

  heading "single: varchar column = number, executed without EXPLAIN, then SHOW WARNINGS"
  session "SELECT * FROM members WHERE code = 1000042; SHOW WARNINGS;"
}

join() {
  local b=$1 label=$2
  heading "join: members a (utf8mb4_unicode_ci) with $b b ($label)"
  local unhinted
  unhinted=$(join_sql "members a JOIN $b b ON b.member_code = a.code")
  explain "$unhinted"
  explain "$(join_sql "members a STRAIGHT_JOIN $b b ON b.member_code = a.code")"
  explain "$(join_sql "$b b STRAIGHT_JOIN members a ON b.member_code = a.code")"
  [ "$b" = b_mb3_unicode ] && explain "$(join_sql "members a STRAIGHT_JOIN $b b FORCE INDEX (idx_member_code) ON b.member_code = a.code")"
  [ "$b" = b_int ] && explain "$(join_sql "$b b STRAIGHT_JOIN members a FORCE INDEX (idx_code) ON b.member_code = a.code")"
  execute "$unhinted"
  [ "$b" = b_mb4_0900 ] && return 0
  execute "$(join_sql "members a STRAIGHT_JOIN $b b ON b.member_code = a.code")"
  execute "$(join_sql "$b b STRAIGHT_JOIN members a ON b.member_code = a.code")"
  [ "$b" = b_int ] || return 0
  heading "join: members a with $b b ($label), filtered on b.batch_id instead of a.account_id"
  local b_filtered="SELECT a.code, b.payload FROM members a JOIN $b b ON b.member_code = a.code WHERE b.batch_id = 42"
  explain "$b_filtered"
  execute "$b_filtered"
}

joins() {
  local base_explain
  base_explain=$(explain "$(join_sql "members a JOIN b_mb4_unicode b ON b.member_code = a.code")")
  grep -qE '\| b +\| .*\| ref +\| idx_member_code ' <<<"$base_explain" || { echo "$base_explain"; invalid "base join does not use b.idx_member_code"; }
  join b_mb4_unicode "base"
  join b_mb3_unicode "b is utf8mb3_unicode_ci"
  [ "$version" = 8.4 ] && join b_mb4_0900 "b is utf8mb4_0900_ai_ci"
  join b_int "b.member_code is INT"
}

laravel_cases() {
  local step stmt
  for step in where-string where-int has-many; do
    heading "laravel: $step"
    laravel "$step"
    stmt=$(logged_select "$step")
    [ -n "$stmt" ] || invalid "no select statement in the slow log for $step"
    echo "EXPLAIN of the logged statement:"
    explain "${stmt%;}"
  done
  heading "laravel: where-date"
  laravel where-date
  heading "laravel: create-table"
  docker compose run --rm -T php php laravel.php create-table
}

staging() {
  heading "staging: mysqldump and restore"
  session "ALTER TABLE b_mb3_unicode DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
SHOW CREATE TABLE b_mb3_unicode\G"
  local dump
  dump=$(mktemp)
  docker compose exec -T mysql mysqldump -uroot --default-character-set=utf8mb4 --single-transaction lab members b_mb3_unicode > "$dump"
  echo "CREATE TABLE statements in the dump:"
  sed -n '/^CREATE TABLE/,/;$/p' "$dump" | tee results/dump-create-tables.sql
  local start=$SECONDS
  docker compose exec -T restore mysql -uroot --default-character-set=utf8mb4 lab < "$dump"
  rm -f "$dump"
  echo "restore seconds: $(( SECONDS - start ))"
  staging_explain mysql "source"
  staging_explain restore "restored, before ANALYZE TABLE"
  session "ANALYZE TABLE members, b_mb3_unicode;" restore
  staging_explain restore "restored, after ANALYZE TABLE"
}

staging_explain() {
  heading "staging: EXPLAIN on $1 ($2)"
  session "SELECT @@hostname, VERSION();" "$1"
  explain "SELECT * FROM members WHERE code = 1000042" "$1"
  explain "$(join_sql "members a JOIN b_mb3_unicode b ON b.member_code = a.code")" "$1"
}

main() {
  set -e
  local start=$SECONDS
  setup
  single
  joins
  if [ "$version" = 8.4 ]; then
    laravel_cases
    staging
  fi
  heading "done"
  echo "total seconds: $(( SECONDS - start ))"
}

rm -rf results
mkdir -p results
set +e
main 2>&1 | tee results/output.txt
rc=${PIPESTATUS[0]}
set -e
docker compose --profile php down -v > /dev/null 2>&1 || true
grep -vE '^(--------------|Bye)$' results/output.txt > results/summary.txt || true
exit "$rc"
