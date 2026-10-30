#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
cond=${1:-}
case $cond in capacity|P[1-5]) ;; *) echo "usage: run-all.sh capacity|P1|P2|P3|P4|P5" >&2; exit 2 ;; esac
. "$here/scripts/versions.env"
. "$here/scripts/capacity.env"
export PATH=$here/.bin:$PATH CLUSTER APP_IMAGE K6_TAG C
start=$(date +%s)
rm -rf "$results"
mkdir -p "$results"
abort() { echo "RUN INVALID: $1" | tee "$results/summary.md"; exit 1; }
py() { python3 -B "$here/scripts/$@"; }
worker=$CLUSTER-worker
cp=$CLUSTER-control-plane

"$here/scripts/setup.sh" > "$results/setup.log" 2>&1 || { tail -30 "$results/setup.log"; abort "setup.sh failed"; }
echo "setup $(( $(date +%s) - start ))s" > "$results/elapsed.txt"

kubectl proxy --port=8001 > "$results/proxy.log" 2>&1 &
proxy=$!
for i in $(seq 50); do curl -fs http://127.0.0.1:8001/version >/dev/null && break; sleep 0.2; done
py poll.py "$results" "$worker" > "$results/poll.log" 2>&1 &
poll=$!
docker exec "$worker" bash -c "$(cat "$here/scripts/recvq.sh")" > "$results/recvq.txt" 2>&1 &
recvq=$!

py render.py app "$cond" > "$results/app.json"
running() { kubectl get pod -l app=app -o jsonpath='{range .items[*]}{.status.containerStatuses[0].state.running.startedAt}{"\n"}{end}' | grep -c .; }
wait_running() {
  for i in $(seq 300); do [ "$(running)" -ge "$1" ] && return 0; sleep 0.2; done
  return 1
}
: > "$results/timeline.txt"
for n in 1 2 3; do
  if [ "$n" = 1 ]; then
    kubectl apply -f "$results/app.json" >/dev/null || abort "apply of the app failed"
  else
    kubectl scale deploy/app --replicas=$n >/dev/null || abort "scale to $n failed"
  fi
  echo "replicas $n $(date +%s.%N)" >> "$results/timeline.txt"
  wait_running "$n" || abort "container $n did not start"
  echo "running $n $(date +%s.%N)" >> "$results/timeline.txt"
  [ "$n" = 3 ] || sleep 3
done
kubectl rollout status deploy/app --timeout=120s >/dev/null || abort "app did not roll out"
kubectl wait --for=condition=Ready pod -l app=app --timeout=60s >/dev/null || abort "app Pods not Ready"
[ "$(kubectl get pod -l app=app -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort -u)" = "$worker" ] || abort "app Pods are not all on $worker"
svc=$(kubectl get svc app -o jsonpath='{.spec.clusterIP}')

{
  echo "\$ kind version"; kind version
  echo "\$ kubectl version"; kubectl version 2>&1
  echo "\$ uname -srm; nproc"; uname -srm; nproc
  echo "\$ gunicorn --version (in the app Pod)"; kubectl exec deploy/app -- gunicorn --version 2>&1
  echo "\$ python -V (in the app Pod)"; kubectl exec deploy/app -- python -V 2>&1
  echo "\$ cat /proc/sys/net/core/somaxconn (in the app Pod)"; kubectl exec deploy/app -- cat /proc/sys/net/core/somaxconn 2>&1
  echo "\$ C"; echo "$C"
  echo "\$ stages (rate seconds fraction)"; py render.py stages "$cond"
  echo "\$ kubectl get nodes -o wide"; kubectl get nodes -o wide
  echo "\$ kubectl get pods -o wide"; kubectl get pods -o wide
  echo "\$ kubectl get deploy app -o jsonpath='{.spec.template.spec}'"; kubectl get deploy app -o jsonpath='{.spec.template.spec}' | python3 -m json.tool
} > "$results/00-environment.txt" 2>&1

echo "measure_start $(date +%s.%N)" >> "$results/timeline.txt"
kubectl create configmap k6-script --from-file=load.js="$here/k6/load.js" >/dev/null
py render.py k6 "$cond" "http://$svc" | kubectl apply -f - >/dev/null || abort "apply of the k6 Job failed"
kubectl wait --for=condition=complete job/k6 --timeout=300s >/dev/null || echo "k6_incomplete $(date +%s.%N)" >> "$results/timeline.txt"
echo "measure_end $(date +%s.%N)" >> "$results/timeline.txt"

touch "$results/.stop"
wait "$poll"
rm -f "$results/.stop"
kill "$recvq" 2>/dev/null
docker exec "$worker" pkill -f 'ss -Hltn' 2>/dev/null
kill "$proxy" 2>/dev/null

mkdir -p "$results/k6" "$results/logs"
docker exec "$cp" tar -C /k6out -cf - . | tar -C "$results/k6" -xf -
kubectl get pods -o wide > "$results/pods-final.txt" 2>&1
kubectl get pods -o json > "$results/pods-final.json"
kubectl get events -o json > "$results/events-final.json"
for p in $(kubectl get pod -l app=app -o name); do kubectl logs "$p" -c app > "$results/logs/${p#pod/}.log" 2>&1; done
docker exec "$worker" journalctl -u kubelet --no-pager -o short-iso-precise > "$results/kubelet.log" 2>&1
echo "total $(( $(date +%s) - start ))s" >> "$results/elapsed.txt"

py analyze.py "$results" "$cond" > "$results/summary.md" || abort "analyze.py failed"
cat "$results/summary.md"
! grep -q '^RUN INVALID' "$results/summary.md"
