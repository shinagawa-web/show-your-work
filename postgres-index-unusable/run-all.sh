#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
all=(explain causes log)
rows=300000
if [ $# -gt 0 ]; then parts=("$@"); else parts=("${all[@]}"); fi
for p in "${parts[@]}"; do
  case " ${all[*]} " in *" $p "*) ;; *) echo "unknown part: $p (one of: ${all[*]})" >&2; exit 2 ;; esac
done

rm -rf results; mkdir -p results
{
  uname -srm; nproc; free -h | head -2
  docker version --format 'docker {{.Server.Version}}'
  docker compose version
  python3 --version
} > results/environment.txt 2>&1
start_all=$(date +%s)
venv=$(mktemp -d)
{ python3 -m venv "$venv" && "$venv/bin/pip" install -q -r requirements.txt && docker compose pull -q postgres; } > results/setup.log 2>&1 || {
  tail -30 results/setup.log; echo "RUN INVALID: setup failed" > results/summary.md; exit 1; }

rc=0
for p in "${parts[@]}"; do
  start=$(date +%s)
  out=results/$p
  mkdir -p "$out"
  docker compose down -v > /dev/null 2>&1
  if docker compose up -d --wait postgres > "$out/setup.log" 2>&1 &&
    docker compose exec -T postgres psql -q -U postgres -d lab -v ON_ERROR_STOP=1 -v rows=$rows -f - < schema.sql >> "$out/setup.log" 2>&1; then
    printf 'rows\t%s\nload_s\t%s\n' "$rows" "$(( $(date +%s) - start ))" > "$out/settings.txt"
    "$venv/bin/python" scripts/lab.py "$p" "$out" > "$out/output.txt" 2>&1 || { rc=1; tail -20 "$out/output.txt"; }
  else
    echo "RUN INVALID: database setup failed" >> "$out/invalid.txt"
    tail -20 "$out/setup.log"
    rc=1
  fi
  docker compose logs --no-log-prefix postgres > "$out/postgres.log" 2>&1
  docker compose down -v > /dev/null 2>&1
  printf 'duration_s\t%s\n' "$(( $(date +%s) - start ))" >> "$out/settings.txt"
done

printf 'total_s\t%s\n' "$(( $(date +%s) - start_all ))" >> results/environment.txt
"$venv/bin/python" scripts/summary.py results "${parts[@]}" > results/summary.md || rc=1
rm -rf "$venv"
cat results/summary.md
exit $rc
