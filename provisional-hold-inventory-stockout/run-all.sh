#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

RUNS="${RUNS:-3}"
[ $# -gt 0 ] || set -- sim run
for target in "$@"; do
  case "$target" in
    sim|run) ;;
    *) echo "unknown target: $target (sim or run)" >&2; exit 2 ;;
  esac
done

main() {
  set -e
  status=0
  for target in "$@"; do
    case "$target" in
      sim)
        echo "=== sim.py ==="
        python3 sim.py --runs 30 || status=1
        for stock in 150 200 300; do
          echo "=== sim.py --stock $stock ==="
          python3 sim.py --runs 30 --stock "$stock" || status=1
        done
        ;;
      run)
        docker compose up -d --wait
        docker compose exec -T postgres psql -U postgres -d lab -f /dev/stdin < schema.sql
        echo "=== run.py ==="
        docker compose run --rm runner --runs "$RUNS" || status=1
        docker compose down
        ;;
    esac
  done
  return "$status"
}

rm -rf results
mkdir -p results
set +e
main "$@" | tee results/output.txt
rc=${PIPESTATUS[0]}
set -e
cp results/output.txt results/summary.txt
exit "$rc"
