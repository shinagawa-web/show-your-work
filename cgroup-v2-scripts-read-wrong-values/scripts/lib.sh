NODE=cg2-control-plane
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
ts() { date -u +%H:%M:%S.%N | cut -c1-12; }
node() { docker exec "$NODE" "$@"; }
cid_of() { node crictl ps --name '^app$' --label "io.kubernetes.pod.name=$1" -q | head -n1; }
pid_of() { node crictl inspect "$(cid_of "$1")" | jq -r .info.pid; }
scope_of() { node cat "/proc/$(pid_of "$1")/cgroup" | sed -n 's/^0:://p'; }
