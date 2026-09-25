#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
results=${RESULTS:-$here/results}
. "$here/scripts/versions.env"
export PATH=$here/.bin:$PATH
RUNS=${RUNS:-app-uid0:legacy app-uid0:default app-uid0-gid0:legacy app-uid0-gid0:default}
NODE=ded-control-plane
IMG=$NETSHOOT_TAG
out=$results/uid0
mkdir -p "$out"

node() { docker exec "$NODE" "$@"; }
wait_eph() {
  local pod=$1 name=$2 i s
  for i in $(seq 90); do
    s=$(kubectl get pod "$pod" -o json | jq -c --arg n "$name" '.status.ephemeralContainerStatuses[]? | select(.name == $n) | .state')
    case "$s" in *running*|*terminated*) break ;; esac
    sleep 1
  done
  echo "$s"
}
target_pid() {
  local sb id
  sb=$(node crictl pods --name "^$1\$" --namespace default -q | head -n1)
  id=$(node crictl ps --pod "$sb" --name '^app$' -q)
  node crictl inspect "$id" | jq -r .info.pid
}

kubectl apply -f "$here/k8s/app.yaml" >/dev/null
kubectl delete -f "$here/k8s/app-uid0.yaml" --ignore-not-found --wait=true >/dev/null 2>&1
kubectl apply -f "$here/k8s/app-uid0.yaml" >/dev/null
for p in $(printf '%s\n' $RUNS | cut -d: -f1 | sort -u); do
  kubectl wait --for=condition=Ready "pod/$p" --timeout=120s >/dev/null || exit 1
done

for run in $RUNS; do
  pod=${run%%:*} prof=${run#*:}
  name=dbg-$prof-$(date +%s)
  args=(--target=app)
  [ "$prof" = default ] || args+=(--profile="$prof")
  {
    echo "\$ kubectl debug $pod --image=$IMG --image-pull-policy=Never -c $name ${args[*]} -- sleep infinity"
    kubectl debug "$pod" --image="$IMG" --image-pull-policy=Never -c "$name" "${args[@]}" -- sleep infinity 2>&1
    echo "[exit $?]"
    state=$(wait_eph "$pod" "$name")
    echo "state: $state"
    echo "spec: $(kubectl get pod "$pod" -o json | jq -c --arg n "$name" '.spec.ephemeralContainers[] | select(.name == $n) | {name, targetContainerName, securityContext}')"
    echo "target securityContext: $(kubectl get pod "$pod" -o json | jq -c '.spec.containers[] | select(.name == "app") | .securityContext')"
    case "$state" in
      *running*) timeout 300 kubectl exec -i "$pod" -c "$name" -- bash -s < "$here/scripts/probe.sh" 2>&1 ;;
      *) echo "=== not_started"; echo "debugger did not start" ;;
    esac
    tp=$(target_pid "$pod")
    echo "## target seen from the node (host pid $tp)"
    echo "\$ grep -E '^(Uid|Gid|Groups|CapInh|CapPrm|CapEff|CapBnd|CapAmb|NoNewPrivs|Seccomp):' /proc/$tp/status"
    node grep -E '^(Uid|Gid|Groups|CapInh|CapPrm|CapEff|CapBnd|CapAmb|NoNewPrivs|Seccomp):' "/proc/$tp/status" 2>&1
    echo "\$ stat -c '%u:%g %n' /proc/$tp"
    node stat -c '%u:%g %n' "/proc/$tp" 2>&1
  } > "$out/$pod-$prof.txt"
done

python3 "$here/scripts/summary.py" "$results" > "$results/summary.md"
