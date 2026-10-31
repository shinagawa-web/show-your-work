#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
runs=3
start=$(date +%s.%N)
el() { awk -v a="$(date +%s.%N)" -v b="$start" 'BEGIN{printf "%.1f", a-b}'; }
all=$(awk -F'\t' 'NR > 1 { print $1 }' "$here/conditions.tsv")
conds=()
while [ $# -gt 0 ]; do
  case $1 in
    -n) [ $# -ge 2 ] && [[ $2 =~ ^[1-9][0-9]*$ ]] || { echo "-n needs a positive count" >&2; exit 2; }; runs=$2; shift 2 ;;
    *) grep -qx -- "$1" <<< "$all" || { echo "unknown condition: $1 (one of: $(echo $all))" >&2; exit 2; }; conds+=("$1"); shift ;;
  esac
done
[ ${#conds[@]} -gt 0 ] || conds=($all)
mkdir -p "$results"
results=$(cd "$results" && pwd)
export RESULTS=$results
. "$here/scripts/versions.env"
docker pull -q "$IMAGE" > /dev/null || exit 1
echo "[$(el)s] image pulled"
status=0
for c in "${conds[@]}"; do
  IFS=$'\t' read -r name keep env trim inject cap < <(awk -F'\t' -v n="$c" '$1 == n' "$here/conditions.tsv")
  pids=()
  for k in $(seq "$runs"); do
    "$here/scripts/run.sh" "$results/$name/run$k" "$keep" "$env" "$trim" "$inject" "$cap" &
    pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p" || status=1; done
  echo "[$(el)s] $name ($runs runs in parallel)"
done
"$here/scripts/summary.sh" "$results" || status=1
echo "[$(el)s] done" | tee "$results/total.txt"
exit "$status"
