#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

all=(hold-0.1 hold-1.0 hold-5.0 conc-1 conc-5 conc-10 conc-20 conc-50 pattern-for-update pattern-conditional)

condition() {
  case "$1" in
    hold-*) echo --pattern for_update --concurrency 20 --hold "${1#hold-}" --duration 30 --init-stock 1000 --runs 2 ;;
    conc-*) echo --pattern for_update --concurrency "${1#conc-}" --hold 0.1 --duration 30 --init-stock 1000 --runs 2 --observe ;;
    pattern-for-update) echo --pattern for_update --concurrency 20 --hold 0.1 --duration 30 --init-stock 1000 --runs 2 ;;
    pattern-conditional) echo --pattern conditional --concurrency 20 --duration 30 --init-stock 200000 --runs 2 ;;
  esac
}

[ $# -gt 0 ] || set -- "${all[@]}"
for c in "$@"; do
  case " ${all[*]} " in *" $c "*) ;; *) echo "unknown condition: $c (one of: ${all[*]})" >&2; exit 2 ;; esac
done

main() {
  set -e
  docker compose up -d --wait
  docker compose exec -T postgres psql -U postgres -d lab -f /dev/stdin < schema.sql
  status=0
  for c in "$@"; do
    args=$(condition "$c")
    echo "=== $args ==="
    docker compose run --rm throughput-runner $args || status=1
  done
  docker compose down
  return "$status"
}

rm -rf results
mkdir -p results
set +e
main "$@" | tee results/output.txt
rc=${PIPESTATUS[0]}
set -e
grep -vE '^  lock_waiters=' results/output.txt > results/summary.txt || true
exit "$rc"
