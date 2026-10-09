#!/bin/sh
# Usage: ./run_all.sh [condition ...]
# With no condition it runs all of them in order. Condition names are listed below.
set -e
cd "$(dirname "$0")"

ALL="hold-0.1 hold-1.0 hold-5.0 conc-1 conc-5 conc-10 conc-20 conc-50 pattern-for-update pattern-conditional"

run() {
  echo "=== $* ==="
  docker compose run --rm throughput-runner "$@"
}

condition() {
  case "$1" in
    # Round 1: hold time sweep, concurrency fixed at 20, 30s steady-state window
    # Theoretical ceiling: TPS = 1/hold. Emerges from system behavior, not thread × hold math.
    hold-*)
      run --pattern for_update --concurrency 20 --hold "${1#hold-}" --duration 30 --init-stock 1000 --runs 2 ;;
    # Round 2: concurrency sweep at hold=0.1 — ceiling should not move as concurrency grows
    conc-*)
      run --pattern for_update --concurrency "${1#conc-}" --hold 0.1 --duration 30 --init-stock 1000 --runs 2 --observe ;;
    # Round 3: pattern comparison at concurrency=20, 30s window
    pattern-for-update)
      run --pattern for_update --concurrency 20 --hold 0.1 --duration 30 --init-stock 1000 --runs 2 ;;
    # conditional needs stock that outlasts the window: 1,500+ TPS × 30s = 45,000+ commits
    pattern-conditional)
      run --pattern conditional --concurrency 20 --duration 30 --init-stock 200000 --runs 2 ;;
    *)
      echo "unknown condition: $1 (one of: $ALL)" >&2; exit 2 ;;
  esac
}

[ $# -eq 0 ] && set -- $ALL
for c in "$@"; do
  case " $ALL " in *" $c "*) ;; *) condition "$c" ;; esac
done

docker compose up -d --wait
docker compose exec -T postgres psql -U postgres -d lab -f /dev/stdin < schema.sql

for c in "$@"; do
  condition "$c"
done

docker compose down
