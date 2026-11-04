#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
. "$here/scripts/lib.sh"
export PATH=$here/.bin:$PATH
out=$1
mkdir -p "$out"
start=$(date +%s)
el() { echo $(( $(date +%s) - start )); }
fail() { echo "RUN INVALID: $1" > "$out/invalid.txt"; echo "RUN INVALID: $1" >&2; exit 1; }
name=ipb
ns=cond
work=$here/.work/causes
rm -rf "$work"; mkdir -p "$work"
declare -A hub_code
for u in "$HUB_USER_BAD_FORMAT" "$HUB_USER_VALID_FORMAT" "$HUB_USER_REAL"; do
  hub_code[$u]=$(curl -sS -o "$out/hub-user-$u.body" -w '%{http_code}' "https://hub.docker.com/v2/users/$u/")
  printf '%s\t%s\n' "$u" "${hub_code[$u]}" >> "$out/hub-user-exists.tsv"
done
[ "${hub_code[$HUB_USER_VALID_FORMAT]}" = 404 ] || fail "Docker Hub did not answer 404 for user name $HUB_USER_VALID_FORMAT (status ${hub_code[$HUB_USER_VALID_FORMAT]}, hub-user-exists.tsv)"
[ "${hub_code[$HUB_USER_REAL]}" = 200 ] || fail "Docker Hub did not answer 200 for user name $HUB_USER_REAL (status ${hub_code[$HUB_USER_REAL]}, hub-user-exists.tsv)"
"$here/scripts/cluster.sh" "$name" "$here/k8s/kind-causes.yaml" "$work" 2> "$out/cluster.log" || fail "cluster setup failed (cluster.log)"
cp "$work/host-proxy.txt" "$out/"
nodes=($(kind get nodes --name "$name" | sort))
other=$(other_arch "$name-worker") || fail "unknown node architecture"
"$here/scripts/push.sh" "$work" "$other" app platform-only single-arch > "$out/images.txt" 2>> "$out/cluster.log" || fail "push failed (cluster.log)"
echo "other_arch=$other" >> "$out/images.txt"
docker exec "$name-worker2" iptables -I OUTPUT -d 192.0.2.1 -j DROP || fail "iptables rule failed"
proxy_found "${nodes[@]}" && fail "a proxy variable with a value is set in containerd's environment on a node (environment.txt)"
kubectl create namespace "$ns" >/dev/null
kubectl create namespace elsewhere >/dev/null
secret "$ns" regcred localhost:5001 "$REG_USER" "$REG_PASSWORD"
secret "$ns" regcred-port-5000 localhost:5000 "$REG_USER" "$REG_PASSWORD"
secret "$ns" regcred-bad-password localhost:5001 "$REG_USER" wrong-password
secret elsewhere regcred-elsewhere localhost:5001 "$REG_USER" "$REG_PASSWORD"
secret "$ns" hub-bad-creds https://index.docker.io/v1/ "$HUB_USER_BAD_FORMAT" "$HUB_PASSWORD"
secret "$ns" hub-bad-creds-valid-name https://index.docker.io/v1/ "$HUB_USER_VALID_FORMAT" "$HUB_PASSWORD"
secret "$ns" hub-wrong-password https://index.docker.io/v1/ "$HUB_USER_REAL" "$HUB_PASSWORD"
for i in $(seq 30); do kubectl -n "$ns" get sa default >/dev/null 2>&1 && break; sleep 1; done
echo "setup $(el)s" > "$out/elapsed.txt"
{
  r kind version
  r kubectl version
  r kubectl get nodes -o wide
  r docker exec "$name-worker2" iptables -S OUTPUT
  r kubectl -n "$ns" get sa default -o jsonpath='{.imagePullSecrets}'
  r kubectl -n "$ns" get secrets
  r kubectl -n elsewhere get secrets
  for n in "${nodes[@]}"; do node_env "$n"; done
} > "$out/environment.txt" 2>&1
start_watch "$out" "$ns"
sleep 2
python3 -B "$here/scripts/conditions.py" causes "$name" "$ns" > "$out/pods.json"
: > "$out/apply.txt"
for i in $(seq 0 $(( $(jq '.items | length' "$out/pods.json") - 1 ))); do
  jq ".items[$i]" "$out/pods.json" | kubectl apply -f - >> "$out/apply.txt" 2>&1 || fail "apply failed (apply.txt)"
  sleep 0.5
done
applied=$(date +%s)
echo "pods applied $(el)s" >> "$out/elapsed.txt"
specs=()
for c in $(python3 -B "$here/scripts/conditions.py" names); do
  e=$(python3 -B -c "import sys; sys.path.insert(0, '$here/scripts'); import conditions; print([c['expect'] for c in conditions.CAUSES if c['name'] == '$c'][0])")
  specs+=("$c:$e")
done
backoff=$(python3 -B -c "import sys; sys.path.insert(0, '$here/scripts'); import conditions; print(conditions.BACKOFF_POD)")
interval=$(python3 -B -c "import sys; sys.path.insert(0, '$here/scripts'); import conditions; print(conditions.INTERVAL_POD)")
specs+=("count:$interval:${PULLS:-5}")
captured=0
deadline=${DEADLINE:-270}
while :; do
  if [ $captured -eq 0 ] && [ "$(kubectl -n "$ns" get pod "$backoff" -o jsonpath='{.status.containerStatuses[0].state.waiting.reason}')" = ImagePullBackOff ]; then
    {
      echo "## $(date -u +%H:%M:%S.%N | cut -c1-12)"
      echo
      r kubectl -n "$ns" get pod "$backoff"
      r kubectl -n "$ns" get pod "$backoff" -o jsonpath='{.status.containerStatuses[*].state.waiting.message}'
      r kubectl -n "$ns" describe pod "$backoff"
    } > "$out/backoff-$backoff.txt"
    captured=1
  fi
  w=$(python3 -B "$here/scripts/wait.py" "$out/events.jsonl" "$out/pods.jsonl" "${specs[@]}") && [ $captured -eq 1 ] && break
  if [ $(( $(date +%s) - applied )) -ge "$deadline" ]; then
    echo "deadline ${deadline}s after apply: $w" > "$out/incomplete.txt"
    break
  fi
  sleep 2
done
echo "observed $(el)s" >> "$out/elapsed.txt"
{
  r kubectl -n "$ns" get pods -o wide
  for c in $(python3 -B "$here/scripts/conditions.py" names); do r kubectl -n "$ns" get pod "$c"; done
} > "$out/get-pods.txt"
for c in $(python3 -B "$here/scripts/conditions.py" names); do
  r kubectl -n "$ns" describe pod "$c" >> "$out/describe.txt"
done
{
  r kubectl -n "$ns" logs single-arch-mismatch
  r kubectl -n "$ns" logs single-arch-mismatch --previous
  r kubectl -n "$ns" get pod single-arch-mismatch -o jsonpath='{.status.containerStatuses[0].state}{"\n"}{.status.containerStatuses[0].lastState}{"\n"}'
} > "$out/single-arch.txt"
r kubectl get events -A -o wide --sort-by=.lastTimestamp > "$out/events.txt"
{
  r docker exec "$name-worker2" iptables -L OUTPUT -v -n
  r docker exec "$name-worker2" sh -c 'journalctl -u containerd --no-pager | grep 192.0.2.1'
} > "$out/no-response.txt"
{
  r docker exec "$name-worker" sh -c 'journalctl -u containerd --no-pager | grep -E "auth.docker.io|oauth"'
  for u in "$HUB_USER_BAD_FORMAT" "$HUB_USER_VALID_FORMAT"; do
    r docker exec "$name-worker" curl -sS -w '\n%{http_code}\n' -X POST -d "grant_type=password&username=$u&password=$HUB_PASSWORD&service=registry.docker.io&scope=repository%3Alibrary%2Fbusybox%3Apull&client_id=containerd-client" https://auth.docker.io/token
    r docker exec "$name-worker" curl -sS -w '\n%{http_code}\n' -u "$u:$HUB_PASSWORD" 'https://auth.docker.io/token?scope=repository%3Alibrary%2Fbusybox%3Apull&service=registry.docker.io'
  done
  r docker exec "$name-worker" curl -sS -w '\n%{http_code}\n' -X POST -d "grant_type=password&username=$HUB_USER_REAL&password=$HUB_PASSWORD&service=registry.docker.io&scope=repository%3A$HUB_USER_REAL%2Fimagepullbackoff-private%3Apull&client_id=containerd-client" https://auth.docker.io/token
  r docker exec "$name-worker" curl -sS -w '\n%{http_code}\n' -u "$HUB_USER_REAL:$HUB_PASSWORD" "https://auth.docker.io/token?scope=repository%3A$HUB_USER_REAL%2Fimagepullbackoff-private%3Apull&service=registry.docker.io"
} > "$out/hub-wrong-creds.txt"
stop_watch "$out"
echo "total $(el)s" >> "$out/elapsed.txt"
kind delete cluster --name "$name" >/dev/null 2>&1
docker rm -f kind-registry >/dev/null 2>&1
exit 0
