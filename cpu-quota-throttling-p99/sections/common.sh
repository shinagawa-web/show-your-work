root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
results=$root/results
run() { "$root/scripts/run.sh" "$@"; }
analyze() { python3 "$root/scripts/analyze.py" "$@" >/dev/null; }
report() { python3 "$root/scripts/report.py" "$@"; }
need_s3() {
  if [ ! -f "$results/s3/requests.csv" ]; then
    echo "results/s3 not found. Run sections/s3_repro.sh first" >&2
    exit 1
  fi
  [ -f "$results/s3/summary.json" ] || analyze "$results/s3"
}
claim() { echo "== $1"; }

S5_CONDS=(
  "rps5_w4 0.5 4 5"
  "rps20_w4 0.5 4 20"
  "rps10_w1 0.5 1 10"
)
S9_CONDS=(
  "cpus0.75_w4 0.75 4 10"
  "cpus1.0_w4 1.0 4 10"
  "cpus1.5_w4 1.5 4 10"
  "cpus2.0_w4 2.0 4 10"
)

run_conds() {
  local section=$1 only=${2:-} found=0 c name cpus w rps
  for c in "${CONDS[@]}"; do
    read -r name cpus w rps <<< "$c"
    [ -z "$only" ] || [ "$only" = "$name" ] || continue
    found=1
    run "$results/$section/$name" "$cpus" "$w" "$rps" poisson "${DUR:-60}"
    analyze "$results/$section/$name"
  done
  if [ "$found" = 0 ]; then
    echo "unknown condition: $only" >&2
    exit 2
  fi
}

table_for() {
  local section=$1 c name dirs=()
  if [ -f "$results/s3/requests.csv" ]; then
    [ -f "$results/s3/summary.json" ] || analyze "$results/s3"
    dirs+=("$results/s3")
  else
    echo "results/s3 not found; the table has no s3 row" >&2
  fi
  for c in "${CONDS[@]}"; do
    read -r name _ <<< "$c"
    if [ -f "$results/$section/$name/requests.csv" ]; then
      [ -f "$results/$section/$name/summary.json" ] || analyze "$results/$section/$name"
      dirs+=("$results/$section/$name")
    else
      echo "results/$section/$name not found; skipped" >&2
    fi
  done
  if [ ${#dirs[@]} -eq 0 ]; then
    echo "no results to tabulate for $section" >&2
    return 0
  fi
  report table "${dirs[@]}"
}
