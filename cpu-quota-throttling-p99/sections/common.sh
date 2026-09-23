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
