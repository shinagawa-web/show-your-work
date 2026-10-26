#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
runs=3
start=$(date +%s.%N)
el() { awk -v a="$(date +%s.%N)" -v b="$start" 'BEGIN{printf "%.1f", a-b}'; }
sections=$(awk -F'\t' 'NR > 1 { print $7 }' "$here/conditions.tsv" | awk '!s[$0]++')
targets=()
while [ $# -gt 0 ]; do
  case $1 in
    -n) [ $# -ge 2 ] && [[ $2 =~ ^[1-9][0-9]*$ ]] || { echo "-n needs a positive count" >&2; exit 2; }; runs=$2; shift 2 ;;
    *) targets+=("$1"); shift ;;
  esac
done
[ ${#targets[@]} -gt 0 ] || targets=($sections)
conds=()
for t in "${targets[@]}"; do
  found=$(awk -F'\t' -v s="${t%%:*}" -v n="${t#*:}" -v one="$([[ $t == *:* ]] && echo 1)" 'NR > 1 && $7 == s && (one == "" || $1 == n) { print $1 }' "$here/conditions.tsv")
  [ -n "$found" ] || { echo "unknown target: $t (a section: $(echo $sections), or <section>:<condition>)" >&2; exit 2; }
  conds+=($found)
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
  IFS=$'\t' read -r name jdk dopts jopts after length section < <(awk -F'\t' -v n="$c" '$1 == n' "$here/conditions.tsv")
  for k in $(seq "$runs"); do
    if [ "$length" = select ]; then
      PEAK_S=2 AFTER_S=1 "$here/scripts/run.sh" "$results/$name/run$k" "$jdk" "$dopts" "$jopts" "$after" || status=1
    else
      "$here/scripts/run.sh" "$results/$name/run$k" "$jdk" "$dopts" "$jopts" "$after" || status=1
    fi
    echo "[$(el)s] $name run$k"
  done
done
"$here/scripts/summary.sh" "$results" "${conds[@]}" || status=1
echo "[$(el)s] done" | tee "$results/total.txt"
exit "$status"
