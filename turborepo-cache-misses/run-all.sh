#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
start=$(date +%s.%N)
el() { awk -v a="$(date +%s.%N)" -v b="$start" 'BEGIN{printf "%.1f", a-b}'; }
. "$here/scripts/versions.env"
tsv=$here/conditions.tsv
jobs=$(awk -F'\t' 'NR > 1 { print $2 }' "$tsv" | awk '!s[$0]++')
conds=()
for t in "$@"; do
  found=$(awk -F'\t' -v t="$t" 'NR > 1 && ($1 == t || $2 == t) { print $1 }' "$tsv")
  [ -n "$found" ] || { echo "unknown target: $t (a job: $(echo $jobs), or a condition P1 to P10)" >&2; exit 2; }
  conds+=($found)
done
[ ${#conds[@]} -gt 0 ] || conds=($(awk -F'\t' 'NR > 1 { print $1 }' "$tsv"))
chains=$(awk -F'\t' -v want=" ${conds[*]} " '
  NR > 1 { after[$1] = $3; order[++n] = $1 }
  END {
    for (i = 1; i <= n; i++) if (index(want, " " order[i] " ")) { c = order[i]; while (after[c] != "-") { c = after[c]; need[c] = 1 } need[order[i]] = 1 }
    for (i = 1; i <= n; i++) { c = order[i]; if (!need[c]) continue; r = c; while (after[r] != "-") r = after[r]; if (!(r in line)) roots[++m] = r; line[r] = line[r] (line[r] == "" ? "" : " ") c }
    for (i = 1; i <= m; i++) print line[roots[i]]
  }' "$tsv")
mkdir -p "$results"
results=$(cd "$results" && pwd)
find "$results" -mindepth 1 -delete
node=tcm-$$-node
cleanup() {
  docker exec "$node" chown -R "$(id -u):$(id -g)" /results > /dev/null 2>&1
  docker rm -f "$node" > /dev/null 2>&1
}
trap cleanup EXIT
docker pull -q "$NODE_IMAGE" > /dev/null || exit 1
docker run -d --name "$node" -v "$here:/src:ro" -v "$results:/results" "$NODE_IMAGE" sleep infinity > /dev/null || exit 1
docker exec "$node" /src/scripts/prepare.sh > "$results/prepare.log" 2>&1 || { tail -30 "$results/prepare.log"; exit 1; }
echo "[$(el)s] workspace installed"
echo "$chains" | xargs -P 4 -L 1 docker exec "$node" /src/scripts/cond.sh
echo "[$(el)s] conditions done"
for c in $chains; do
  case " ${conds[*]} " in *" $c "*) ;; *) docker exec "$node" rm -rf "/results/$c" ;; esac
done
status=0
docker exec "$node" node /src/scripts/summary.mjs /results ${conds[*]} || status=1
echo "[$(el)s] done" | tee "$results/total.txt"
exit "$status"
