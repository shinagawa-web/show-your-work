#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
out=$1 keep=$2 env=$3 trim=$4 inject=$5 cap=$6
cycles=${CYCLES:-16}
peak_n=${PEAK_N:-100}
obj=${OBJ_BYTES:-1048576}
hold_s=${HOLD_S:-1.5}
wait_s=${WAIT_S:-12}
pre_s=${PRE_INJECT_S:-3}
post_s=${POST_INJECT_S:-10}
opts=()
[ "$env" = - ] || opts+=(-e "$env")
[ "$cap" = - ] || opts+=(--cap-add="$cap")
mkdir -p "$out"; out=$(cd "$out" && pwd)
chmod 777 "$out"
rm -f "$out/events.txt" "$out/inject.txt"
name=glibc-heap-not-returned-$$
args=("$inject" "$pre_s" "$post_s" "$keep" "$trim" "$cycles" "$peak_n" "$obj" "$hold_s" "$wait_s")
printf 'image=%s\ndocker_opts=%s\nobserver_args=%s\n' "$IMAGE" "${opts[*]:--}" "${args[*]}" > "$out/settings.txt"
start=$(date +%s.%N)
docker run --rm --name "$name" "${opts[@]}" -v "$here/app:/app:ro" -v "$out:/out" "$IMAGE" python /app/observer.py "${args[@]}" > "$out/observer-stdout.txt" 2>&1 &
dpid=$!
if [ "$inject" = 1 ]; then
  idle=
  while kill -0 "$dpid" 2>/dev/null; do
    idle=$(awk '$1 == "idle" { print $2; exit }' "$out/events.txt" 2>/dev/null)
    [ -n "$idle" ] && break
    sleep 0.2
  done
  if [ -n "$idle" ]; then
    pid=$(awk '$1 == "start" { print $3; exit }' "$out/events.txt")
    until awk -v a="$(date +%s.%N)" -v b="$idle" -v p="$pre_s" 'BEGIN { exit !(a >= b + p) }'; do sleep 0.1; done
    docker exec "$name" python /app/inject.py "$pid" > "$out/inject-stdout.txt" 2>&1
  fi
fi
wait "$dpid"
rc=$?
awk -v a="$(date +%s.%N)" -v b="$start" -v rc="$rc" 'BEGIN{printf "container_s %.1f\nexit %d\n", a-b, rc}' > "$out/container.txt"
exit "$rc"
