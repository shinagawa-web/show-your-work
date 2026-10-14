#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
. "$here/scripts/versions.env"
. "$here/scripts/lib.sh"
export PATH=$here/.bin:$PATH
start=$(date +%s)
rm -rf "$results"
mkdir -p "$results"
abort() { echo "RUN INVALID: $1" | tee "$results/summary.md"; exit 1; }
conds=(c1-1:scale4 c1-2:scale4 c1-3:scale4 c2:scale4 c3:scale4 c4:scale3 c5:scale1 c6:scale7 c7:apply4 c8:minreplicas4 c9:disable-scale4)
names=("${conds[@]%%:*}")
OFFSET=${OFFSET:-7}
kcm=kube-controller-manager-hpa-control-plane

"$here/scripts/setup.sh" || abort "setup.sh failed"
kubectl apply -f "$here/.bin/metrics-server-$METRICS_SERVER_VERSION.yaml" >/dev/null || abort "metrics-server apply failed"
kubectl -n kube-system patch deploy metrics-server --type=json -p "[{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args/-\",\"value\":\"--kubelet-insecure-tls\"},{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/image\",\"value\":\"$METRICS_SERVER_IMAGE\"}]" >/dev/null || abort "metrics-server patch failed"
for n in "${names[@]}"; do
  render "$(app_of "$n")" "$n" 2 | kubectl apply -f - >/dev/null || abort "apply of Deployment $n failed"
  render "$(hpa_of "$n")" "$n" | kubectl apply -f - >/dev/null || abort "apply of HPA $n failed"
done
kubectl -n kube-system rollout status deploy/metrics-server --timeout=120s >/dev/null || abort "metrics-server did not roll out"
for n in "${names[@]}"; do
  for i in $(seq 150); do
    kubectl get hpa "$n" -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}' 2>/dev/null | grep -q . && break
    sleep 1
  done
  kubectl get hpa "$n" -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}' | grep -q . || abort "HPA $n did not read metrics"
done
echo "ready $(( $(date +%s) - start ))s" > "$results/elapsed.txt"

{
  r kind version
  r kubectl version
  r kubectl -n kube-system get deploy metrics-server -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}{.spec.template.spec.containers[0].args}{"\n"}'
  r kubectl -n kube-system get pod "$kcm" -o jsonpath='{.spec.containers[0].command}{"\n"}'
  r kubectl get hpa
  r kubectl get deploy
} > "$results/00-environment.txt"

last=$(kubectl -n kube-system logs "$kcm" --since=30s | python3 -B "$here/scripts/syncs.py" c1-1 | awk '$3 == "proposing" || $3 == "decided" {t=$1} END {print t}')
[ -n "$last" ] || abort "no HPA sync of c1-1 in the kube-controller-manager log"
at=$(python3 -c '
import sys, time
from datetime import datetime, timezone
h, m, s = sys.argv[1].split(":")
now = time.time()
d = datetime.now(timezone.utc).replace(hour=int(h), minute=int(m), second=0, microsecond=0).timestamp() + float(s)
if d > now + 43200: d -= 86400
t = d + float(sys.argv[2])
while t < now + 2: t += 15
print(f"{t:.3f}")
' "$last" "$OFFSET")
echo "last sync of c1-1 before the operations: $last; operations at $(date -u -d "@$at" +%H:%M:%S.%3N) (OFFSET=${OFFSET}s after a sync)" > "$results/01-timing.txt"

python3 -B "$here/scripts/poll.py" "$results" &
poll=$!
pids=()
for c in "${conds[@]}"; do
  "$here/scripts/cond.sh" "${c%%:*}" "${c#*:}" "$at" > "$results/${c%%:*}.txt" 2>&1 &
  pids+=($!)
done
for p in "${pids[@]}"; do wait "$p"; done
touch "$results/.stop"
wait "$poll"
rm -f "$results/.stop"

kubectl -n kube-system logs "$kcm" > "$results/kube-controller-manager.log" 2>&1
python3 -B "$here/scripts/syncs.py" "${names[@]}" < "$results/kube-controller-manager.log" > "$results/sync.log"
for n in "${names[@]}"; do
  echo "## $n"
  kubectl get events -o json | jq -r --arg n "$n" '.items | sort_by(.lastTimestamp // .eventTime) | .[] | select(.involvedObject.name == $n) | "\(.firstTimestamp // .eventTime) \(.lastTimestamp // .eventTime) x\(.count // 1) \(.involvedObject.kind) \(.reason) \(.source.component // .reportingComponent) \(.message)"'
done > "$results/events.txt"
echo "total $(( $(date +%s) - start ))s" >> "$results/elapsed.txt"

python3 -B "$here/scripts/summary.py" "$results" "${names[@]}" > "$results/summary.md" || abort "summary.py failed"
cat "$results/summary.md"
! grep -q '^RUN INVALID' "$results/summary.md"
