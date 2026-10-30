#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
out=$1 keep=$2 env=$3 trim=$4 inject=$5
cycles=${CYCLES:-16}
peak_n=${PEAK_N:-100}
obj=${OBJ_BYTES:-1048576}
hold_s=${HOLD_S:-1.5}
wait_s=${WAIT_S:-12}
pre_s=${PRE_INJECT_S:-3}
post_s=${POST_INJECT_S:-10}
envopt=()
[ "$env" = - ] || envopt=(-e "$env")
mkdir -p "$out"; out=$(cd "$out" && pwd)
chmod 777 "$out"
args=("$inject" "$pre_s" "$post_s" "$keep" "$trim" "$cycles" "$peak_n" "$obj" "$hold_s" "$wait_s")
printf 'image=%s\ndocker_opts=%s\nobserver_args=%s\n' "$IMAGE" "${envopt[*]:--}" "${args[*]}" > "$out/settings.txt"
start=$(date +%s.%N)
docker run --rm "${envopt[@]}" -v "$here/app:/app:ro" -v "$out:/out" "$IMAGE" python /app/observer.py "${args[@]}" > "$out/observer-stdout.txt" 2>&1
rc=$?
awk -v a="$(date +%s.%N)" -v b="$start" -v rc="$rc" 'BEGIN{printf "container_s %.1f\nexit %d\n", a-b, rc}' > "$out/container.txt"
exit "$rc"
