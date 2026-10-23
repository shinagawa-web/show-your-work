#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
runs=${RUNS:-3}
start=$(date +%s.%N)
el() { awk -v a="$(date +%s.%N)" -v b="$start" 'BEGIN{printf "%.1f", a-b}'; }
all=$(awk -F'\t' 'NR > 1 { print $1 }' "$here/conditions.tsv")
conds=("$@")
[ ${#conds[@]} -gt 0 ] || conds=($all)
for c in "${conds[@]}"; do
  grep -qx "$c" <<< "$all" || { echo "unknown condition: $c (one of: $(echo $all))" >&2; exit 2; }
done
mkdir -p "$results"
results=$(cd "$results" && pwd)
export RESULTS=$results
. "$here/scripts/versions.env"
imgs=("$JDK8_IMAGE")
for c in "${conds[@]}"; do
  v=JDK$(awk -F'\t' -v n="$c" '$1 == n { print $2 }' "$here/conditions.tsv")_IMAGE
  imgs+=("${!v}")
done
for img in $(printf '%s\n' "${imgs[@]}" | sort -u); do docker pull -q "$img" > /dev/null || exit 1; done
"$here/scripts/build.sh" || exit 1
echo "[$(el)s] images pulled, program built"
status=0
for c in "${conds[@]}"; do
  IFS=$'\t' read -r name jdk dopts jopts after length < <(awk -F'\t' -v n="$c" '$1 == n' "$here/conditions.tsv")
  for k in $(seq "$runs"); do
    if [ "$length" = select ]; then
      PEAK_S=2 AFTER_S=1 "$here/scripts/run.sh" "$results/$name/run$k" "$jdk" "$dopts" "$jopts" "$after" || status=1
    else
      "$here/scripts/run.sh" "$results/$name/run$k" "$jdk" "$dopts" "$jopts" "$after" || status=1
    fi
    echo "[$(el)s] $name run$k"
  done
done
python3 "$here/scripts/summarize.py" --md "$results" "${conds[@]}" > "$results/summary.md" || status=1
echo "[$(el)s] done" | tee "$results/total.txt"
exit "$status"
