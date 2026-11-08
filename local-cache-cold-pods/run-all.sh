#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
all="app-restart-local app-restart-redis redis-restart-none redis-restart-none-probe redis-restart-rdb-probe sentinel-restart"
conds=() overrides=()
for a in "$@"; do
  case $a in
    [A-Z]*=*) overrides+=("$a") ;;
    *) case " $all " in *" $a "*) conds+=("$a") ;; *) echo "usage: run-all.sh [$all] [NAME=value ...]" >&2; exit 2 ;; esac ;;
  esac
done
[ ${#conds[@]} -gt 0 ] || read -ra conds <<< "$all"
rm -rf "$results"
mkdir -p "$results"
rc=0
reuse=
[ ${#conds[@]} -eq 1 ] || reuse=first
for c in "${conds[@]}"; do
  begin=$(date +%s)
  RESULTS=$results/$c REUSE=$reuse timeout 300 "$here/scripts/condition.sh" "$c" ${overrides[@]+"${overrides[@]}"}
  code=$?
  [ "$code" -eq 0 ] || rc=1
  echo "$c $(( $(date +%s) - begin )) $code" >> "$results/times.txt"
  [ -z "$reuse" ] || reuse=yes
done
python3 -B "$here/scripts/report.py" "$results"/*/ > "$results/summary.md" || rc=1
cat "$results/summary.md"
exit "$rc"
