#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
. "$here/scripts/lib.sh"
name=$1 op=$2 at=$3
WINDOW=${WINDOW:-360}
python3 -c 'import sys, time; time.sleep(max(0, float(sys.argv[1]) - time.time()))' "$at"
echo "## before $(ts)"
fields "$name"
op_ts=$(ts)
echo "## operation $op_ts"
opout=$(case $op in
  scale1) r kubectl scale "deploy/$name" --replicas=1 ;;
  scale3) r kubectl scale "deploy/$name" --replicas=3 ;;
  scale4) r kubectl scale "deploy/$name" --replicas=4 ;;
  scale7) r kubectl scale "deploy/$name" --replicas=7 ;;
  apply4) render "$(app_of "$name")" "$name" 4 | r kubectl apply -f - ;;
  minreplicas4) r kubectl patch hpa "$name" --type=merge -p '{"spec":{"minReplicas":4}}' ;;
  disable-scale4)
    r kubectl patch hpa "$name" --type=merge -p '{"spec":{"behavior":{"scaleDown":{"selectPolicy":"Disabled"}}}}'
    r kubectl scale "deploy/$name" --replicas=4
    ;;
  *) echo "unknown operation $op" ;;
esac)
echo "$opout"
case $opout in "unknown operation"*) exit 1 ;; esac
screens=
if [ -n "${SCREENS:-}" ] && [[ " c1-1 c3 c4 c7 c10 " == *" $name "* ]]; then
  "$here/scripts/screens.sh" "$name" "$op_ts" "$(( $(date +%s) + WINDOW ))" "$opout" >> "$SCREENS/screen-$name.txt" 2>&1 &
  screens=$!
fi
echo "## after operation $(ts)"
fields "$name"
last=$(spec_replicas "$name")
start=$(date +%s)
until=$((start + WINDOW))
ss=
while [ "$(date +%s)" -lt "$until" ]; do
  cur=$(spec_replicas "$name")
  if [ -n "$cur" ] && [ "$cur" != "$last" ]; then
    echo "## change $(ts) spec.replicas $last -> $cur"
    fields "$name"
    last=$cur
    if [ "$op" = apply4 ] && [ -z "$ss" ]; then
      echo "## server-side $(ts)"
      render "$(app_of "$name")" "$name" 4 | r kubectl apply --server-side -f -
      fields "$name"
      ss=$(date +%s)
      [ $((ss + 20)) -gt "$until" ] && until=$((ss + 20))
    fi
  fi
  sleep 1
done
if [ "$op" = apply4 ] && [ -z "$ss" ]; then
  echo "## server-side $(ts)"
  render "$(app_of "$name")" "$name" 4 | r kubectl apply --server-side -f -
  fields "$name"
  sleep 5
fi
echo "## end $(ts)"
fields "$name"
[ -n "$screens" ] && wait "$screens"
exit 0
