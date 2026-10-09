#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

run() {
  echo "=== $* ==="
  docker compose run --rm runner "$@" || status=1
}

main() {
  set -e
  docker compose up -d --wait
  docker compose exec -T postgres psql -U postgres -d lab -f /dev/stdin < schema.sql
  status=0
  run --pattern A --concurrency 1  --keys 1    --init-stock 20 --runs 3
  run --pattern B --concurrency 1  --keys 1    --init-stock 20 --runs 3
  run --pattern A --concurrency 50 --keys 1    --init-stock 20 --wait 0.1 --runs 5
  run --pattern B --concurrency 50 --keys 1    --init-stock 20 --wait 0.1 --runs 5
  run --pattern C --concurrency 50 --keys 1    --init-stock 20 --wait 0.1 --runs 5
  run --pattern CB --concurrency 50 --keys 1   --init-stock 20 --wait 0.1 --runs 5
  run --pattern D --concurrency 50 --keys 1    --init-stock 20 --wait 0.1 --runs 5
  run --pattern A --concurrency 50 --keys 1000 --init-stock 5  --wait 0.1 --runs 5
  run --pattern B --concurrency 50 --keys 1000 --init-stock 5  --wait 0.1 --runs 5
  docker compose down
  return "$status"
}

rm -rf results
mkdir -p results
set +e
main | tee results/output.txt
rc=${PIPESTATUS[0]}
set -e
grep -E '^(===|pattern=| +run| +[0-9]+ |RUN INVALID)' results/output.txt > results/summary.txt || true
exit "$rc"
