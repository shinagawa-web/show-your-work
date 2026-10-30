#!/bin/bash
host=$1 port=$2 mode=$3
export MYSQL_PWD=root
deadline=$(( $(date +%s) + 180 ))
while :; do
  t=$(date +%s.%N)
  out=$(mysql -h "$host" -P "$port" -uroot -N -B -e 'SELECT 1' 2>&1)
  rc=$?
  printf '%s\t%s\t%s\n' "$t" "$rc" "${out//$'\n'/\\n}"
  [ "$mode" = poll ] || break
  [ "$rc" -ne 0 ] || break
  [ "$(date +%s)" -lt "$deadline" ] || break
  sleep "$(awk -v t="$t" -v n="$(date +%s.%N)" 'BEGIN { s = t + 0.1 - n; print (s > 0 ? s : 0) }')"
done
exit "$rc"
