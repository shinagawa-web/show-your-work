r() {
  echo "\$ $*"
  "$@" 2>&1
  echo "[exit $?]"
  echo
}
other_arch() {
  case $(docker exec "$1" uname -m) in aarch64) echo amd64 ;; x86_64) echo arm64 ;; *) return 1 ;; esac
}
node_env() {
  local node=$1
  echo "## $node"
  echo
  r docker exec "$node" uname -m
  r docker exec "$node" containerd --version
  r docker exec "$node" kubelet --version
  echo "\$ docker exec $node sh -c 'tr \"\\0\" \"\\n\" < /proc/\$(pidof containerd)/environ | grep -i proxy'"
  docker exec "$node" sh -c 'tr "\0" "\n" < /proc/$(pidof containerd)/environ | grep -i proxy' 2>&1
  echo "[exit $?]"
  echo
  r docker exec "$node" sh -c 'ls /etc/systemd/system/containerd.service.d/ 2>&1; cat /etc/systemd/system/containerd.service.d/* 2>/dev/null'
  r docker exec "$node" ls -la /proc/sys/fs/binfmt_misc
  echo "\$ docker exec $node sh -c 'for f in /proc/sys/fs/binfmt_misc/*; do echo \"== \$f\"; cat \"\$f\"; done'"
  docker exec "$node" sh -c 'for f in /proc/sys/fs/binfmt_misc/*; do [ "${f##*/}" = register ] && continue; echo "== $f"; cat "$f"; done' 2>&1
  echo
  r docker exec "$node" sh -c 'containerd config dump | grep -n -E "config_path|discard_unpacked_layers|use_local_image_pull"'
  r docker exec "$node" sh -c 'ls -R /etc/containerd/certs.d; cat /etc/containerd/certs.d/*/hosts.toml'
  r kubectl get --raw "/api/v1/nodes/$node/proxy/configz"
}
proxy_found() {
  local node
  for node in "$@"; do
    docker exec "$node" sh -c 'tr "\0" "\n" < /proc/$(pidof containerd)/environ | grep -Eqi "^[a-z_]*proxy=."' && return 0
  done
  return 1
}
start_watch() {
  local dir=$1 ns=$2
  : > "$dir/events.jsonl"
  : > "$dir/pods.jsonl"
  rm -f "$dir/.stop"
  kubectl get events -A --watch -o json 2>>"$dir/watch.err" | python3 -B "$here/scripts/watch.py" "$dir/events.jsonl" &
  watch_pid=$!
  python3 -B "$here/scripts/poll.py" "$ns" "$dir/pods.jsonl" "$dir/.stop" &
  poll_pid=$!
}
stop_watch() {
  local dir=$1
  touch "$dir/.stop"
  wait "$poll_pid" 2>/dev/null
  pkill -f "kubectl get events -A --watch" 2>/dev/null
  wait "$watch_pid" 2>/dev/null
  rm -f "$dir/.stop"
}
secret() {
  local ns=$1 name=$2 server=$3 user=$4 pass=$5
  kubectl -n "$ns" create secret docker-registry "$name" --docker-server="$server" --docker-username="$user" --docker-password="$pass" >/dev/null
}
