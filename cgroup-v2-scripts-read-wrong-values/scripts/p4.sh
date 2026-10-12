#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/lib.sh"
RESTARTS=${RESTARTS:-3}
DEADLINE=${DEADLINE:-200}

for i in $(seq 60); do
  uid=$(kubectl get pod oom -o jsonpath='{.metadata.uid}' 2>/dev/null)
  [ -n "$uid" ] && pcg=$(node sh -c "find /sys/fs/cgroup -type d -name '*pod${uid//-/_}.slice'" | sed 's|^/sys/fs/cgroup||')
  [ -n "${pcg:-}" ] && break
  sleep 1
done
echo "pod uid: $uid"
echo "pod slice: $pcg"

show() {
  local d=$1 f
  for f in memory.max memory.oom.group memory.events memory.events.local; do
    echo "-- $d/$f"
    node cat "/sys/fs/cgroup$d/$f" 2>&1
  done
}

last=
start=$(date +%s)
while :; do
  st=$(kubectl get pod oom -o json | jq -c '.status.containerStatuses[0] | {restartCount, containerID, state: (.state | keys[0]), reason: (.state[] | .reason // null), lastState: .lastState.terminated}')
  key=$(echo "$st" | jq -r '"\(.containerID) \(.state)"')
  if [ "$key" != "$last" ]; then
    last=$key
    echo
    echo "## $(ts) $st"
    cid=$(echo "$st" | jq -r '.containerID // "" | sub("^containerd://"; "")')
    echo "-- scopes under the pod slice"
    node sh -c "ls -d /sys/fs/cgroup$pcg/*.scope" 2>&1 | sed 's|^/sys/fs/cgroup||'
    if [ "$(echo "$st" | jq -r .state)" = running ] && [ -n "$cid" ]; then
      show "$pcg/cri-containerd-$cid.scope"
    elif [ -n "$cid" ]; then
      echo "-- scope of $cid"
      node ls -d "/sys/fs/cgroup$pcg/cri-containerd-$cid.scope" 2>&1
    fi
    show "$pcg"
    rc=$(echo "$st" | jq -r .restartCount)
    [ "$rc" -ge "$RESTARTS" ] && [ "$(echo "$st" | jq -r .state)" = running ] && break
  fi
  [ $(( $(date +%s) - start )) -ge "$DEADLINE" ] && { echo "deadline reached"; break; }
  sleep 1
done

echo
echo "sandbox: $(node crictl pods --label io.kubernetes.pod.uid="$uid" -q)"
echo
echo "## kernel log"
sudo dmesg 2>&1 | grep -E 'oom-kill:|Memory cgroup out of memory|memory.oom.group' | grep -B1 -A3 "pod${uid//-/_}"
