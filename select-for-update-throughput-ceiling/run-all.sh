#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
mapfile -t all < <(awk -F'\t' 'NR > 1 { print $1 }' conditions.tsv)
if [ $# -gt 0 ]; then conds=("$@"); else conds=("${all[@]}"); fi
for c in "${conds[@]}"; do
  case " ${all[*]} " in *" $c "*) ;; *) echo "unknown condition: $c (one of: ${all[*]})" >&2; exit 2 ;; esac
done

rm -rf results; mkdir -p results
{
  uname -srm; nproc; free -h | head -2
  docker version --format 'docker {{.Server.Version}}'
  docker compose version
} > results/environment.txt 2>&1
docker compose build runner > results/build.log 2>&1 && docker compose pull postgres >> results/build.log 2>&1 || {
  tail -30 results/build.log; echo "RUN INVALID: build failed" > results/summary.md; exit 1; }

rc=0
for c in "${conds[@]}"; do
  start=$(date +%s)
  out=results/$c
  mkdir -p "$out"
  read -r rate pool work work_at plain_rate < <(awk -F'\t' -v n="$c" '$1 == n { print $2, $3, $4, $5, $6 }' conditions.tsv)
  printf 'rate\t%s\npool\t%s\nwork\t%s\nwork_at\t%s\nplain_rate\t%s\n' "$rate" "$pool" "$work" "$work_at" "$plain_rate" > "$out/settings.txt"
  docker compose down -v > /dev/null 2>&1
  docker compose up -d --wait postgres > "$out/compose.log" 2>&1
  docker compose exec -T postgres psql -q -U postgres -d lab -v ON_ERROR_STOP=1 -f - < schema.sql >> "$out/compose.log" 2>&1
  docker compose exec -T postgres psql -At -U postgres -d lab -c 'SELECT version()' -c 'SHOW max_connections' > "$out/postgres.txt" 2>&1
  id=$(docker compose ps -q postgres)
  cg=$(find /sys/fs/cgroup -type d -name "*$id*" 2>/dev/null | head -1)
  python3 scripts/cpu_sample.py "$cg/cpu.stat" > "$out/cpu.tsv" 2> "$out/cpu.err" &
  sampler=$!
  docker compose run --rm --user "$(id -u):$(id -g)" -v "$here/$out":/out runner \
    --rate "$rate" --pool "$pool" --work "$work" --work-at "$work_at" --plain-rate "$plain_rate" --out /out \
    > "$out/server.log" 2>&1 || { echo "RUN INVALID: server.py failed" >> "$out/analysis.txt"; tail -20 "$out/server.log"; rc=1; }
  kill "$sampler"
  docker compose logs postgres > "$out/postgres.log" 2>&1
  docker compose down -v > /dev/null 2>&1
  python3 scripts/analyze.py "$out" >> "$out/analysis.txt" 2>&1 || rc=1
  printf 'duration_s\t%s\n' "$(( $(date +%s) - start ))" >> "$out/settings.txt"
done

{
  echo '```'; cat results/environment.txt; echo '```'
  for c in "${conds[@]}"; do
    echo "# $c"
    echo '```'; cat "results/$c/settings.txt" "results/$c/postgres.txt"; echo; cat "results/$c/analysis.txt" 2>/dev/null || echo "no analysis"; echo '```'
  done
} > results/summary.md
cat results/summary.md
exit $rc
