#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
. "$here/scripts/versions.env"
export PATH=$here/.bin:$PATH
PROFILES=${PROFILES:-default legacy general baseline restricted netadmin sysadmin}
NODE=ded-control-plane
IMG=$NETSHOOT_TAG

"$here/scripts/setup.sh" || exit 1
rm -rf "$results"
mkdir -p "$results/profiles"

r() {
  echo "\$ $*"
  "$@" 2>&1
  echo "[exit $?]"
}
rs() {
  echo "\$ $1"
  bash -o pipefail -c "$1" 2>&1
  echo "[exit $?]"
}
node() { docker exec "$NODE" "$@"; }
wait_eph() {
  local pod=$1 name=$2 i s
  for i in $(seq 90); do
    s=$(kubectl get pod "$pod" -o json | jq -c --arg n "$name" '.status.ephemeralContainerStatuses[]? | select(.name == $n) | .state')
    case "$s" in
      *running*|*terminated*) break ;;
      *waiting*) echo "$s" | grep -q ContainerCreating || { [ "$i" -ge 10 ] && break; } ;;
    esac
    sleep 1
  done
  echo "$s"
}
eph_spec() {
  kubectl get pod "$1" -o json | jq -c --arg n "$2" '.spec.ephemeralContainers[] | select(.name == $n) | {name, targetContainerName, securityContext}'
}
debug_bg() {
  local name=$1; shift
  r kubectl debug app --image="$IMG" --image-pull-policy=Never -c "$name" "$@" -- sleep infinity
}
show() {
  echo "state: $(wait_eph "$1" "$2")"
  echo "spec: $(eph_spec "$1" "$2")"
}
tty_session() {
  local cmd=$1 input=$2
  echo "\$ $cmd"
  echo "(typed into the session: $input)"
  { sleep 15; printf '%s\n' "$input"; sleep 20; } | timeout 90 script -qec "stty cols 400; $cmd" /dev/null 2>&1 | tr -d '\r'
  echo "[exit ${PIPESTATUS[1]}]"
}
pid_of() { node crictl inspect "$1" | jq -r .info.pid; }

{
  r uname -r
  rs 'stat -fc %T /sys/fs/cgroup'
  r kind version
  r kubectl version
  r node crictl version
  r node containerd --version
  rs 'kubectl debug --help | sed -n "/--profile=/,/--quiet/p"'
  rs "docker image inspect $IMG distroless-app:dev --format '{{.RepoTags}} {{.RepoDigests}} {{.Id}}'"
  rs "grep '^FROM' $here/app/Dockerfile"
} > "$results/00-environment.txt"

kubectl delete pod app crash crash-copy crash-copy2 --ignore-not-found --wait=true >/dev/null 2>&1
kubectl apply -f "$here/k8s/app.yaml" >/dev/null
kubectl wait --for=condition=Ready pod/app --timeout=120s >/dev/null || exit 1

{
  echo "## 1. kubectl exec into distroless"
  r kubectl exec app -c app -- sh
  r kubectl exec -it app -c app -- sh
  r kubectl get pod app -o jsonpath='{.status.qosClass}{"\n"}'
} > "$results/01-exec.txt"

{
  echo "## 2. ps with and without --target"
  debug_bg dbg-notarget
  show app dbg-notarget
  rs "kubectl exec app -c dbg-notarget -- ps auxf"
  debug_bg dbg-default --target=app
  show app dbg-default
  rs "kubectl exec app -c dbg-default -- ps auxf"
  echo "## literal interactive form from the issue"
  tty_session "kubectl debug -it app --image=$IMG --target=app -c dbg-it -- bash" 'ps auxf; exit'
  echo "state: $(wait_eph app dbg-it)"
} > "$results/02-target.txt"

for p in $PROFILES; do
  out=$results/profiles/$p.txt
  name=dbg-$p
  args=(--target=app)
  [ "$p" = default ] || args+=(--profile="$p")
  {
    if kubectl get pod app -o json | jq -e --arg n "$name" '.spec.ephemeralContainers[]? | select(.name == $n)' >/dev/null; then
      echo "reusing $name from 02-target.txt"
    else
      debug_bg "$name" "${args[@]}"
    fi
    state=$(wait_eph app "$name")
    echo "state: $state"
    echo "spec: $(eph_spec app "$name")"
    case "$state" in
      *running*) timeout 300 kubectl exec -i app -c "$name" -- bash -s < "$here/scripts/probe.sh" 2>&1 ;;
      *) echo "=== not_started"; echo "debugger did not start" ;;
    esac
  } > "$out"
done

{
  echo "## namespaces from the node (crictl + readlink /proc/<pid>/ns/*)"
  sb=$(node crictl pods --name '^app$' --namespace default -q | head -n1)
  echo "sandbox $sb pid $(node crictl inspectp "$sb" | jq -r .info.pid)"
  rows="node-init:1 pause:$(node crictl inspectp "$sb" | jq -r .info.pid)"
  for id in $(node crictl ps --pod "$sb" -q); do
    n=$(node crictl inspect "$id" | jq -r .status.metadata.name)
    rows="$rows $n:$(pid_of "$id")"
  done
  printf '%-14s %-6s' container pid
  for ns in net ipc uts pid mnt cgroup user time; do printf ' %-22s' "$ns"; done
  echo
  for row in $rows; do
    n=${row%%:*} pid=${row#*:}
    printf '%-14s %-6s' "$n" "$pid"
    for ns in net ipc uts pid mnt cgroup user time; do printf ' %-22s' "$(node readlink /proc/$pid/ns/$ns)"; done
    echo
  done
  echo
  echo "## cgroup of app seen from the node"
  aid=$(node crictl ps --pod "$sb" --name '^app$' -q)
  apid=$(pid_of "$aid")
  rs "docker exec $NODE cat /proc/$apid/cgroup"
  cg=$(node cat /proc/$apid/cgroup | cut -d: -f3)
  rs "docker exec $NODE cat /sys/fs/cgroup$cg/cpu.max"
  rs "docker exec $NODE cat /sys/fs/cgroup$cg/cpu.stat"
  did=$(node crictl ps --pod "$sb" --name '^dbg-default$' -q)
  rs "docker exec $NODE cat /proc/$(pid_of "$did")/cgroup"
} > "$results/03-namespaces.txt"

{
  echo "## A/B/C in dbg-default"
  timeout 300 kubectl exec -i app -c dbg-default -- bash -s < "$here/scripts/scenarios.sh" 2>&1
} > "$results/06-scenarios-default.txt"
if kubectl get pod app -o json | jq -e '.status.ephemeralContainerStatuses[] | select(.name == "dbg-sysadmin") | .state.running' >/dev/null; then
  {
    echo "## A/B/C in dbg-sysadmin"
    timeout 300 kubectl exec -i app -c dbg-sysadmin -- bash -s < "$here/scripts/scenarios.sh" 2>&1
  } > "$results/06-scenarios-sysadmin.txt"
fi

{
  echo "## ephemeral containers cannot be removed"
  r kubectl get pod app -o jsonpath='{range .spec.ephemeralContainers[*]}{.name}{"\n"}{end}'
  rs "kubectl get pod app -o json | jq '.spec.ephemeralContainers = []' | kubectl replace --subresource=ephemeralcontainers -f -"
  r kubectl patch pod app --subresource=ephemeralcontainers --type=json -p '[{"op":"remove","path":"/spec/ephemeralContainers/0"}]'
  echo "## exited ephemeral container stays in the spec"
  r kubectl debug app --image="$IMG" --image-pull-policy=Never -c dbg-exit --target=app -- true
  echo "state: $(wait_eph app dbg-exit)"
  sleep 3
  r kubectl get pod app -o jsonpath='{range .status.ephemeralContainerStatuses[?(@.name=="dbg-exit")]}{.name} {.state}{"\n"}{end}'
  r kubectl get pod app -o jsonpath='{.spec.ephemeralContainers[*].name}{"\n"}'
  r kubectl debug app --image="$IMG" --image-pull-policy=Never -c dbg-exit --target=app -- true
  echo "## resources on an ephemeral container"
  rs "kubectl get pod app -o json | jq --arg img '$IMG' '.spec.ephemeralContainers += [{name: \"dbg-res\", image: \$img, imagePullPolicy: \"Never\", command: [\"sleep\", \"infinity\"], resources: {limits: {cpu: \"100m\", memory: \"64Mi\"}}}]' | kubectl replace --subresource=ephemeralcontainers -f -"
  echo "## QoS class after adding ephemeral containers"
  r kubectl get pod app -o jsonpath='{.status.qosClass}{"\n"}'
  r kubectl get pod app -o jsonpath='{range .spec.containers[*]}{.name} {.resources}{"\n"}{end}{range .spec.ephemeralContainers[*]}{.name} {.resources}{"\n"}{end}'
  echo "## CrashLoopBackOff pod"
  kubectl apply -f "$here/k8s/crash.yaml" >/dev/null
  for i in $(seq 90); do
    kubectl get pod crash -o jsonpath='{.status.containerStatuses[0].state.waiting.reason}' | grep -q CrashLoopBackOff && break
    sleep 2
  done
  r kubectl get pod crash
  r kubectl debug crash --image="$IMG" --image-pull-policy=Never -c dbg --target=app -- sleep infinity
  show crash dbg
  r kubectl exec crash -c dbg -- ps auxf
  r kubectl debug crash --image="$IMG" --image-pull-policy=Never -c dbg2 -- sleep infinity
  show crash dbg2
  r kubectl exec crash -c dbg2 -- ps auxf
  echo "## --copy-to with --set-image"
  r kubectl debug crash --copy-to=crash-copy --set-image=app="$IMG"
  sleep 20
  r kubectl get pod crash-copy -o jsonpath='{range .spec.containers[*]}{.name} {.image} {.args}{"\n"}{end}'
  r kubectl get pod crash-copy
  r kubectl get pod crash-copy -o jsonpath='{.status.containerStatuses[0].lastState}{"\n"}'
  r kubectl debug crash --copy-to=crash-copy2 --container=app --image="$IMG" --image-pull-policy=Never -- sleep infinity
  kubectl wait --for=condition=Ready pod/crash-copy2 --timeout=60s >/dev/null
  r kubectl get pod crash-copy2 -o jsonpath='{range .spec.containers[*]}{.name} {.image} {.command} {.args}{"\n"}{end}'
  r kubectl get pod crash-copy2
} > "$results/05-constraints.txt"

{
  echo "## node debug, literal form from the issue"
  sb=$(node crictl pods --name '^app$' --namespace default -q | head -n1)
  aid=$(node crictl ps --pod "$sb" --name '^app$' -q)
  cg=$(node cat /proc/$(pid_of "$aid")/cgroup | cut -d: -f3)
  kubectl delete pod -l app.kubernetes.io/managed-by=kubectl-debug --ignore-not-found >/dev/null 2>&1
  tty_session "kubectl debug node/$NODE -it --profile=sysadmin --image=$IMG -- bash" "id; ls --color=never /host; cat /host/proc/1/cgroup; cat /sys/fs/cgroup$cg/cpu.max; cat /host/sys/fs/cgroup$cg/cpu.max; cat /host/sys/fs/cgroup$cg/cpu.stat; exit"
  r kubectl get pods -o wide --field-selector spec.nodeName="$NODE"
  np=$(kubectl get pods -o name | grep node-debugger | head -n1)
  [ -z "$np" ] || rs "kubectl get $np -o json | jq '{hostPID: .spec.hostPID, hostNetwork: .spec.hostNetwork, hostIPC: .spec.hostIPC, sc: .spec.containers[0].securityContext, mounts: .spec.containers[0].volumeMounts, volumes: .spec.volumes}'"
} > "$results/07-node.txt"

RESULTS=$results "$here/scripts/uid0.sh"

python3 "$here/scripts/summary.py" "$results" > "$results/summary.md"
cat "$results/summary.md"
