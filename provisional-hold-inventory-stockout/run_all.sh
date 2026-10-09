#!/bin/sh
# Usage: ./run_all.sh [sim] [run]
# With no target it runs both. RUNS sets the number of run.py rounds (default 3).
set -e
cd "$(dirname "$0")"

RUNS="${RUNS:-3}"
[ $# -eq 0 ] && set -- sim run

for target in "$@"; do
  case "$target" in
    sim|run) ;;
    *) echo "unknown target: $target (sim or run)" >&2; exit 2 ;;
  esac
done

for target in "$@"; do
  case "$target" in
    sim)
      echo "=== sim.py ==="
      python3 sim.py --runs 30

      # Same scenario with more stock, to see where lost sales stop depending on T
      for stock in 150 200 300; do
        echo "=== sim.py --stock $stock ==="
        python3 sim.py --runs 30 --stock "$stock"
      done
      ;;
    run)
      docker compose up -d --wait
      docker compose exec -T postgres psql -U postgres -d lab -f /dev/stdin < schema.sql

      echo "=== run.py ==="
      docker compose run --rm runner --runs "$RUNS"

      docker compose down
      ;;
  esac
done
