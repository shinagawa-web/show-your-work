#!/bin/sh
set -eu
app=$1 front=$2 pause=$3
{
  echo "workers $(pgrep -P "$front" | tr '\n' ' ')"
  nsenter -t "$front" -n ss -Htnp state established 'dport = :8081'
} > /results/established-before-pause.txt
nsenter -t "$app" -n nstat -asz TcpExtListenOverflows TcpExtListenDrops > /results/nstat-before.txt
kill -STOP "$app"
echo "stop $(date +%s.%N)" >> /results/timeline.txt
sleep "$pause"
kill -CONT "$app"
echo "cont $(date +%s.%N)" >> /results/timeline.txt
