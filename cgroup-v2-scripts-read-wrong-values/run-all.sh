#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
. "$here/scripts/versions.env"
. "$here/scripts/lib.sh"
export PATH=$here/.bin:$PATH
start=$(date +%s)

"$here/scripts/setup.sh" || exit 1
rm -rf "$results"
mkdir -p "$results"

kubectl delete -f "$here/k8s/pods.yaml" --ignore-not-found --wait=true >/dev/null 2>&1
kubectl apply -f "$here/k8s/pods.yaml" >/dev/null
"$here/scripts/p4.sh" > "$results/p4-oom-kill.txt" 2>&1 &
p4=$!
kubectl wait --for=condition=Ready pod/throttle pod/cache pod/weight --timeout=120s >/dev/null || exit 1

{
  r uname -r
  r cat /sys/kernel/mm/lru_gen/enabled
  rs 'stat -fc %T /sys/fs/cgroup'
  r kind version
  r kubectl version
  r node crictl version
  r node containerd --version
  r node runc --version
  r node cat /sys/kernel/mm/lru_gen/enabled
  r node stat -fc %T /sys/fs/cgroup
  r node grep cgroup2 /proc/self/mountinfo
  rs "docker image inspect $BUSYBOX_TAG --format '{{.RepoTags}} {{.RepoDigests}} {{.Id}}'"
} > "$results/00-environment.txt"

{
  echo "## v1 controller directories on the node"
  r node ls -d /sys/fs/cgroup/cpu /sys/fs/cgroup/cpu,cpuacct /sys/fs/cgroup/memory
  r node ls /sys/fs/cgroup
  echo "## v1 paths inside the container"
  r kubectl exec throttle -- cat /sys/fs/cgroup/cpu/cpu.stat
  r kubectl exec throttle -- cat /sys/fs/cgroup/cpu,cpuacct/cpu.stat
  r kubectl exec cache -- cat /sys/fs/cgroup/memory/memory.stat
  r kubectl exec throttle -- ls /sys/fs/cgroup
} > "$results/01-symptom.txt"

{
  for pod in throttle weight; do
    cg=$(scope_of "$pod")
    echo "## $pod"
    r node cat "/proc/$(pid_of "$pod")/cgroup"
    r kubectl exec "$pod" -- cat /proc/self/cgroup
    for i in 1 2 3; do
      echo "node   $(ts)"; node cat "/sys/fs/cgroup$cg/cpu.stat" 2>&1
      echo "exec   $(ts)"; kubectl exec "$pod" -- cat /sys/fs/cgroup/cpu.stat 2>&1
      echo "node   $(ts)"; node cat "/sys/fs/cgroup$cg/cpu.stat" 2>&1
      echo
    done
  done
} > "$results/p1-cpu-stat-inside-vs-node.txt"

{
  echo "## container scope of throttle, read inside the container"
  r kubectl exec throttle -- cat /sys/fs/cgroup/cpu.max
  r kubectl exec throttle -- cat /sys/fs/cgroup/cpu.stat
  echo "## v1 key"
  r kubectl exec throttle -- grep throttled_time /sys/fs/cgroup/cpu.stat
  echo "## v2 key"
  r kubectl exec throttle -- grep throttled_usec /sys/fs/cgroup/cpu.stat
  echo "## seconds: v1 formula as written, key renamed with the ns divisor kept, and the us divisor"
  rs "kubectl exec throttle -- awk '\$1==\"throttled_time\"{printf \"%.6f\\n\", \$2/1e9}' /sys/fs/cgroup/cpu.stat"
  rs "kubectl exec throttle -- awk '\$1==\"throttled_usec\"{u=\$2; printf \"throttled_usec=%d ns_divisor=%.6f us_divisor=%.6f\\n\", u, u/1e9, u/1e6}' /sys/fs/cgroup/cpu.stat"
  echo "## nr_throttled / nr_periods"
  rs "kubectl exec throttle -- awk '\$1==\"nr_periods\"{p=\$2} \$1==\"nr_throttled\"{t=\$2} END{printf \"nr_throttled=%d nr_periods=%d ratio=%.4f\\n\", t, p, t/p}' /sys/fs/cgroup/cpu.stat"
} > "$results/p2-throttled-key.txt"

for i in $(seq 60); do kubectl exec cache -- test -f /data/done 2>/dev/null && break; sleep 1; done
{
  cg=$(scope_of cache)
  pcg=$(dirname "$cg")
  echo "container scope: $cg"
  echo "pod slice: $pcg"
  r kubectl exec cache -- ls -l /data
  echo "## v1 key in memory.stat"
  r node grep -E '^total_' "/sys/fs/cgroup$cg/memory.stat"
  for i in 1 2 3; do
    echo "## sample $i"
    echo "cgroup files $(ts) $(date -u +%s%3N)"
    for d in "$cg" "$pcg"; do
      cur=$(node cat "/sys/fs/cgroup$d/memory.current")
      inact=$(node awk '$1=="inactive_file"{print $2}' "/sys/fs/cgroup$d/memory.stat")
      tinact=$(node awk '$1=="total_inactive_file"{print $2}' "/sys/fs/cgroup$d/memory.stat")
      echo "$d"
      node grep -E '^(anon|file|active_file|inactive_file|active_anon|inactive_anon) ' "/sys/fs/cgroup$d/memory.stat"
      echo "memory.current=$cur inactive_file=$inact total_inactive_file=[$tinact]"
      echo "memory.current-inactive_file=$((cur - inact)) memory.current-total_inactive_file=$((cur - ${tinact:-0}))"
    done
    echo "kubelet /metrics/resource $(ts) $(date -u +%s%3N)"
    kubectl get --raw "/api/v1/nodes/$NODE/proxy/metrics/resource" | grep -E '^(container|pod)_memory_working_set_bytes.*pod="cache"'
    sleep 5
  done
} > "$results/p3-working-set.txt"

{
  cg=$(scope_of weight)
  pcg=$(dirname "$cg")
  r kubectl get pod weight -o jsonpath='{.spec.containers[0].resources}{"\n"}{.status.qosClass}{"\n"}'
  r node runc --version
  r node cat "/sys/fs/cgroup$pcg/cpu.weight"
  r node cat "/sys/fs/cgroup$cg/cpu.weight"
  r kubectl exec weight -- cat /sys/fs/cgroup/cpu.weight
} > "$results/p5-cpu-weight.txt"

{
  pid=$(pid_of weight)
  cid=$(kubectl get pod weight -o jsonpath='{.status.containerStatuses[0].containerID}' | sed 's|^containerd://||')
  cg=$(scope_of weight)
  echo "weight container pid on the node: $pid"
  echo "weight container id from containerStatuses: $cid"
  r node cat "/proc/$pid/cgroup"
  r node cat "/sys/fs/cgroup$cg/cpu.weight"
  r node cat "/sys/fs/cgroup$cg/memory.events"
  kubectl delete pod -l app.kubernetes.io/managed-by=kubectl-debug --ignore-not-found >/dev/null 2>&1
  dbg=$(sed -e "s/@PID@/$pid/g" -e "s/@CID@/$cid/g" "$here/scripts/node-debug.sh")
  r kubectl debug "node/$NODE" --image="$BUSYBOX_TAG" -- sh -c "$dbg"
  dp=$(kubectl get pods -o name | grep node-debugger | head -n1)
  for i in $(seq 60); do
    [ "$(kubectl get "$dp" -o jsonpath='{.status.phase}')" = Succeeded ] && break
    sleep 1
  done
  r kubectl logs "$dp"
  rs "kubectl get $dp -o json | jq -c '{hostPID: .spec.hostPID, hostNetwork: .spec.hostNetwork, hostIPC: .spec.hostIPC, sc: .spec.containers[0].securityContext, mounts: .spec.containers[0].volumeMounts}'"
} > "$results/p6-node-debug-cgroup.txt"

wait "$p4"
echo "elapsed $(( $(date +%s) - start ))s" > "$results/elapsed.txt"

status=0
grep -q '^container scope: ' "$results/p3-working-set.txt" && kubectl exec cache -- test -f /data/done || { echo "cache Pod did not finish reading the file"; status=1; }
grep -q '^deadline reached' "$results/p4-oom-kill.txt" && { echo "oom Pod did not reach the restart count"; status=1; }
grep -q '^\$ cat /proc/[0-9]*/cgroup' "$results/p6-node-debug-cgroup.txt" || { echo "node debugger produced no output"; status=1; }

python3 "$here/scripts/summary.py" "$results" > "$results/summary.md" || { echo "summary.py failed"; status=1; }
cat "$results/summary.md"
exit "$status"
