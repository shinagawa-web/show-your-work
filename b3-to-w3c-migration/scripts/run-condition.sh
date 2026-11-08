#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
name=$1
out=results/$name
mkdir -p "$out"
IFS=$'\t' read -r _ _ envoy a b c requests ratio sends _ < <(awk -F'\t' -v n="$name" '$1 == n' conditions.tsv)

export ENVOY=$envoy A_PROPAGATORS=$a B_PROPAGATORS=$b C_PROPAGATORS=$c B_MANUAL_FORWARD=0
export SAMPLER=parentbased_always_on SAMPLER_ARG=
if [ "$b" = manual ]; then B_PROPAGATORS=none B_MANUAL_FORWARD=1; fi
if [ "$ratio" != - ]; then SAMPLER=parentbased_traceidratio SAMPLER_ARG=$ratio; fi
printf 'ENVOY=%s\nA_PROPAGATORS=%s\nB_PROPAGATORS=%s\nC_PROPAGATORS=%s\nB_MANUAL_FORWARD=%s\nOTEL_TRACES_SAMPLER=%s\nOTEL_TRACES_SAMPLER_ARG=%s\n' \
  "$ENVOY" "$A_PROPAGATORS" "$B_PROPAGATORS" "$C_PROPAGATORS" "$B_MANUAL_FORWARD" "$SAMPLER" "$SAMPLER_ARG" > "$out/settings.env"

project=b3w3c-$name
dc() { docker compose -p "$project" "$@"; }
trap 'dc logs --no-color > "$out/compose.log" 2>&1; dc down -v --remove-orphans > /dev/null 2>&1' EXIT

dc up -d --wait > "$out/up.log" 2>&1 || { echo "compose up failed" > "$out/invalid.txt"; exit 1; }

addr() { dc port "$1" "$2"; }
jaeger=$(addr jaeger 16686)
gateway=$(addr envoy 8080)
admin=$(addr envoy 9901)
declare -A direct=([a]=$(addr a 8080) [b]=$(addr b 8080) [c]=$(addr c 8080))

wait_ok() {
  for _ in $(seq 150); do
    curl -fs -o /dev/null --max-time 1 "$1" && return 0
    sleep 0.2
  done
  echo "not ready: $1" > "$out/invalid.txt"
  exit 1
}
wait_ok "http://$jaeger/api/v3/services"
wait_ok "http://$admin/ready"
for s in a b c; do wait_ok "http://${direct[$s]}/healthz"; done

: > "$out/requests.tsv"
for i in $(seq "$requests"); do
  t0=$(date +%s%N)
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://$gateway/")
  t1=$(date +%s%N)
  printf '%s\t%s\t%s\t%s\n' "$i" "$t0" "$t1" "$code" >> "$out/requests.tsv"
done

sleep 2
python3 -B scripts/collect.py search "$jaeger" "$out/traces"
dc logs --no-color --no-log-prefix postgres > "$out/postgres.log" 2>&1

parent=b7ad6b7169203331
send() {
  curl -s -o /dev/null -H "traceparent: 00-$3-$parent-01" "http://$2/"
  printf '%s\t%s\n' "$1" "$3" >> "$out/sends.tsv"
}
case $sends in
  direct)
    send a "${direct[a]}" 0af7651916cd43dd8448eb211c80319a
    send b "${direct[b]}" 0af7651916cd43dd8448eb211c80319b
    send c "${direct[c]}" 0af7651916cd43dd8448eb211c80319c
    ;;
  gateway)
    send gateway "$gateway" 0af7651916cd43dd8448eb211c80319d
    ;;
esac
if [ "$sends" != - ]; then
  sleep 2
  while IFS=$'\t' read -r target id; do
    python3 -B scripts/collect.py trace "$jaeger" "$id" "$out/traces/sent-$target.json"
  done < "$out/sends.tsv"
fi

python3 -B scripts/analyze.py "$out"
