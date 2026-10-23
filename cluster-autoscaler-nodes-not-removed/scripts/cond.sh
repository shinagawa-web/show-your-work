#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
cond=$1
out=$2
. "$here/scripts/versions.env"
. "$here/scripts/lib.sh"
export PATH=$here/.bin:$PATH
mkdir -p "$out"
: > "$out/events.log"
invalid() { echo "RUN INVALID: $1" > "$out/invalid.txt"; mark "invalid $1"; collect; exit 1; }

ca_image=$CA_IMAGE
bsp=(--blocking-system-pod-distruption-timeout=${BSP_TIMEOUT:-60s})
extra=()
MIN_COUNT=0
extra_node=0
SIDECAR=0
lower_cpu=after
lower_mem=after
expect_removal=0
window=90
loops_before=2
after_removal=20
stage2=0
pre=()
case $cond in
  base) expect_removal=1; window=120 ;;
  p1) extra_node=1 ;;
  p2) MIN_COUNT=3; window=60 ;;
  p3) pre=(annotate) ;;
  p4) lower_cpu=before; window=60 ;;
  p5) SIDECAR=1; window=60 ;;
  p6) pre=(custom-controller) ;;
  p7) pre=(kube-system-deployment); expect_removal=1; window=170 ;;
  p8) pre=(kube-system-deployment); ca_image=$CA_IMAGE_1_32; bsp=(); window=170 ;;
  p9) pre=(not-safe-to-evict) ;;
  p10) pre=(metrics-server); expect_removal=1; window=170 ;;
  p11) pre=(metrics-server metrics-server-pdb); window=170 ;;
  p12) pre=(metrics-server metrics-server-pdb); extra=(--skip-nodes-with-local-storage=false); expect_removal=1; window=170 ;;
  p13) pre=(emptydir-memory); expect_removal=1; window=120 ;;
  p14) pre=(hostpath) ;;
  p15) pre=(single-replica-pdb); window=100 ;;
  p16) pre=(anti-affinity) ;;
  p17) pre=(anti-affinity bare-pod) ;;
  p19) pre=(single-replica-pdb); lower_cpu=before; stage2=1; window=40 ;;
  *) echo "unknown condition $cond" >&2; exit 2 ;;
esac
export SIDECAR MIN_COUNT

ca_cmd=(./cluster-autoscaler --cloud-provider=kwok --namespace=kube-system --logtostderr=true --stderrthreshold=info --v=4
  --scale-down-unneeded-time=30s --scale-down-delay-after-add=30s --unremovable-node-recheck-timeout=${RECHECK:-60s} "${bsp[@]}" "${extra[@]}")

poller=
streamer=
collect() {
  [ -n "$poller" ] && kill "$poller" 2>/dev/null
  [ -n "$streamer" ] && kill "$streamer" 2>/dev/null
  kubectl -n kube-system logs deploy/cluster-autoscaler > "$out/ca.log" 2>&1
  kubectl -n kube-system get pod -l app=cluster-autoscaler -o jsonpath='{range .items[*]}{.metadata.name} restarts={.status.containerStatuses[0].restartCount}{"\n"}{end}' > "$out/ca-pod.txt" 2>&1
  kubectl -n kube-system get configmap cluster-autoscaler-status -o jsonpath='{.data.status}' > "$out/ca-status.txt" 2>&1
  kubectl get nodes -o wide > "$out/nodes-final.txt" 2>&1
  kubectl get pdb -A > "$out/pdb-final.txt" 2>&1
  kubectl get events -A --sort-by=.lastTimestamp > "$out/events-k8s.txt" 2>&1
  pods_table final
}
pods_table() {
  kubectl get pods -A --sort-by=.spec.nodeName -o custom-columns='NAMESPACE:.metadata.namespace,NAME:.metadata.name,NODE:.spec.nodeName,CREATED:.metadata.creationTimestamp,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp' > "$out/pods-$1.txt" 2>&1
}
poll_nodes() {
  local prev= cur
  while :; do
    cur=$(node_names)
    if [ "$cur" != "$prev" ]; then echo "$(ts) $(echo $cur | wc -w) $cur" >> "$out/nodes.log"; prev=$cur; fi
    sleep 1
  done
}
loops() { local n; n=$(grep -c "Scale down status:" "$out/ca-stream.log" 2>/dev/null); echo "${n:-0}"; }
wait_loops() {
  local n=$1 limit=$2 start=$(loops) t=0
  while [ $(( $(loops) - start )) -lt "$n" ]; do
    sleep 1; t=$((t + 1)); [ $t -lt "$limit" ] || return 1
  done
}
guard() {
  local ns=$1 sel=$2 i
  for i in $(seq 90); do
    [ "$(kubectl -n "$ns" get pods -l "$sel" -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' 2>/dev/null | grep -c True)" -ge 3 ] && break
    sleep 1
  done
  kubectl -n "$ns" get pods -l "$sel" -o json | python3 -B "$here/scripts/placement.py" "$sel" >> "$out/placement.txt"
}
place_after() {
  [ "$extra_node" = 1 ] && return 0
  echo "after lowering:" >> "$out/placement.txt"
  local d
  for d in web api worker; do guard default app=$d || return 1; done
}
pre_action() {
  case $1 in
    annotate) kubectl annotate node ng-a-0 cluster-autoscaler.kubernetes.io/scale-down-disabled=true >/dev/null ;;
    custom-controller)
      kubectl apply -f "$here/k8s/conditions/custom-controller.yaml" >/dev/null || return 1
      kubectl wait --for condition=established crd/widgets.example.com --timeout=60s >/dev/null || return 1
      for i in 0 2; do
        render "$here/k8s/conditions/bare-pod.yaml" | sed -e "s|@NAME@|standalone-$i|g" -e "s|@NODE@|ng-a-$i|g" | kubectl apply -f - >/dev/null || return 1
      done
      render "$here/k8s/conditions/custom-controller-pod.yaml" | sed -e "s|@NAME@|widget-runner-1|g" -e "s|@NODE@|ng-a-1|g" -e "/^---$/,\$d" | kubectl apply -f - >/dev/null || return 1
      local uid
      uid=$(kubectl get widget widget-runner-1 -o jsonpath='{.metadata.uid}') || return 1
      render "$here/k8s/conditions/custom-controller-pod.yaml" | sed -e "s|@NAME@|widget-runner-1|g" -e "s|@NODE@|ng-a-1|g" -e "s|@UID@|$uid|g" | kubectl apply -f - >/dev/null || return 1
      kubectl wait --for=condition=Ready pod standalone-0 standalone-2 widget-runner-1 --timeout=60s >/dev/null ;;
    bare-pod)
      render "$here/k8s/conditions/bare-pod.yaml" | sed -e "s|@NAME@|standalone-0|g" -e "s|@NODE@|ng-a-0|g" | kubectl apply -f - >/dev/null || return 1
      kubectl wait --for=condition=Ready pod standalone-0 --timeout=60s >/dev/null ;;
    kube-system-deployment) render "$here/k8s/conditions/$1.yaml" | kubectl apply -f - >/dev/null && guard kube-system app=cluster-agent ;;
    not-safe-to-evict) render "$here/k8s/conditions/$1.yaml" | kubectl apply -f - >/dev/null && guard default app=batch-runner ;;
    metrics-server) render "$here/k8s/conditions/$1.yaml" | kubectl apply -f - >/dev/null && guard kube-system app.kubernetes.io/name=metrics-server ;;
    metrics-server-pdb) kubectl apply -f "$here/k8s/conditions/$1.yaml" >/dev/null ;;
    emptydir-memory) render "$here/k8s/conditions/$1.yaml" | kubectl apply -f - >/dev/null && guard default app=session-cache ;;
    hostpath) render "$here/k8s/conditions/$1.yaml" | kubectl apply -f - >/dev/null && guard default app=host-reader ;;
    single-replica-pdb) render "$here/k8s/conditions/$1.yaml" | kubectl apply -f - >/dev/null && guard default tier=single ;;
    anti-affinity) render "$here/k8s/conditions/$1.yaml" | kubectl apply -f - >/dev/null && guard default app=one-per-node ;;
  esac
}

mark "setup start"
"$here/scripts/setup.sh" > "$out/setup.log" 2>&1 || { echo "RUN INVALID: setup.sh failed" > "$out/invalid.txt"; exit 1; }
mark "cluster up"
cp="ca-control-plane"
kubectl taint node "$cp" node-role.kubernetes.io/control-plane=:NoSchedule --overwrite >/dev/null || invalid "taint of the control plane failed"
affinity='{"spec":{"template":{"spec":{"affinity":{"nodeAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":{"nodeSelectorTerms":[{"matchExpressions":[{"key":"type","operator":"NotIn","values":["kwok"]}]}]}}}}}}}'
for ds in kindnet kube-proxy; do kubectl -n kube-system patch ds "$ds" --type=merge -p "$affinity" >/dev/null || invalid "patch of $ds failed"; done
sed -e "s|image: registry.k8s.io/kwok/kwok:$KWOK_VERSION|image: $KWOK_IMAGE|" "$here/.bin/kwok-$KWOK_VERSION.yaml" | kubectl apply -f - >/dev/null || invalid "kwok apply failed"
kubectl -n kube-system patch deploy kwok-controller --type=merge -p '{"spec":{"template":{"spec":{"nodeSelector":{"node-role.kubernetes.io/control-plane":""},"tolerations":[{"key":"node-role.kubernetes.io/control-plane","effect":"NoSchedule"}]}}}}' >/dev/null || invalid "kwok patch failed"
kubectl wait --for condition=established crd/stages.kwok.x-k8s.io --timeout=60s >/dev/null || invalid "kwok CRDs not established"
kubectl apply -f "$here/.bin/stage-fast-$KWOK_VERSION.yaml" >/dev/null || invalid "kwok stages apply failed"
render "$here/k8s/kube-state-metrics.yaml" | kubectl apply -f - >/dev/null || invalid "kube-state-metrics apply failed"
render "$here/k8s/kwok-provider.yaml" | kubectl apply -f - >/dev/null || invalid "kwok provider config apply failed"
kubectl -n kube-system rollout status deploy/kwok-controller --timeout=120s >/dev/null || invalid "kwok-controller did not roll out"

nodes=(ng-a-0 ng-a-1 ng-a-2)
for n in "${nodes[@]}"; do
  sed -e "s|@NAME@|$n|g" -e "s|@NODEGROUP@|ng-a|g" -e "s|^@NODEGROUP_ANNOTATION@|    cluster-autoscaler.kwok.nodegroup/name: ng-a|" "$here/k8s/node.yaml" | kubectl apply -f - >/dev/null || invalid "node $n apply failed"
done
if [ "$extra_node" = 1 ]; then
  sed -e "s|@NAME@|ng-b-0|g" -e "s|@NODEGROUP@|ng-b|g" -e "/^@NODEGROUP_ANNOTATION@/d" "$here/k8s/node.yaml" | kubectl apply -f - >/dev/null || invalid "node ng-b-0 apply failed"
fi
kubectl wait --for=condition=Ready node -l type=kwok --timeout=60s >/dev/null || invalid "kwok nodes not Ready"
mark "nodes ready: $(node_names)"

render "$here/k8s/daemonsets.yaml" | kubectl apply -f - >/dev/null || invalid "DaemonSets apply failed"
for ds in log-collector node-exporter; do kubectl rollout status ds/$ds --timeout=60s >/dev/null || invalid "DaemonSet $ds did not roll out"; done
for d in web api worker; do
  python3 -B "$here/scripts/apps.py" before before "$SIDECAR" "$d" "$PAUSE_IMAGE" | kubectl apply -f - >/dev/null || invalid "apply of $d failed"
  kubectl rollout status deploy/$d --timeout=60s >/dev/null || invalid "$d did not roll out"
done
if [ "$extra_node" = 0 ]; then
  for d in web api worker; do guard default app=$d || invalid "placement of $d is not one Pod per node (placement.txt)"; done
fi
kubectl -n kube-system rollout status deploy/kube-state-metrics --timeout=90s >/dev/null || invalid "kube-state-metrics did not roll out"
mark "workloads placed"

awk -v repl="$(printf '            - %s\n' "${ca_cmd[@]}")" '{ if ($0 == "@CA_COMMAND@") print repl; else print }' "$here/k8s/cluster-autoscaler.yaml" | sed -e "s|@CA_IMAGE@|$ca_image|" > "$out/cluster-autoscaler.yaml"
kubectl apply -f "$out/cluster-autoscaler.yaml" >/dev/null || invalid "cluster-autoscaler apply failed"
kubectl -n kube-system rollout status deploy/cluster-autoscaler --timeout=120s >/dev/null || invalid "cluster-autoscaler did not roll out"
mark "ca started"
kubectl -n kube-system logs -f deploy/cluster-autoscaler > "$out/ca-stream.log" 2>&1 &
streamer=$!
poll_nodes &
poller=$!
wait_loops "$loops_before" 90 || invalid "no $loops_before scale-down loops of the cluster autoscaler within 90s"
snapshot before || invalid "kube-state-metrics snapshot failed"
mark "before checked"

for a in "${pre[@]}"; do
  mark "pre $a"
  pre_action "$a" || invalid "condition step $a failed"
done
pods_table pre
mark "lower start cpu=$lower_cpu memory=$lower_mem"
lower_apps "$lower_cpu" "$lower_mem" || invalid "rollout of lowered requests failed"
mark "lower done"
place_after || invalid "placement after lowering is not one Pod per node (placement.txt)"
snapshot after || invalid "kube-state-metrics snapshot failed"
pods_table after

observe() {
  local until=$(( $(date +%s) + $1 )) n0 n removed_at=
  n0=$(node_names | wc -w)
  while [ "$(date +%s)" -lt "$until" ]; do
    sleep 1
    n=$(node_names | wc -w)
    if [ "$expect_removal" = 1 ] && [ -z "$removed_at" ] && [ "$n" -lt "$n0" ]; then
      removed_at=$(date +%s)
      mark "node count $n0 -> $n"
      until=$(( removed_at + after_removal < until ? removed_at + after_removal : until ))
    fi
  done
}
observe "$window"
if [ "$stage2" = 1 ]; then
  snapshot stage1 || invalid "kube-state-metrics snapshot failed"
  mark "lower2 start cpu=after memory=after"
  lower_apps after after || invalid "rollout of lowered CPU requests failed"
  mark "lower2 done"
  place_after || invalid "placement after lowering is not one Pod per node (placement.txt)"
  snapshot stage2 || invalid "kube-state-metrics snapshot failed"
  observe 70
fi
snapshot end || invalid "kube-state-metrics snapshot failed"
mark "end"
collect
grep -q 'restarts=0' "$out/ca-pod.txt" || { echo "RUN INVALID: the cluster-autoscaler container restarted (ca-pod.txt)" > "$out/invalid.txt"; exit 1; }
exit 0
