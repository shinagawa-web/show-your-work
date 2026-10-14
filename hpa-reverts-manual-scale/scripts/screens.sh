#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
. "$here/scripts/lib.sh"
name=$1 op_ts=$2 deadline=$3 opout=${4:-}
kcm=kube-controller-manager-hpa-control-plane
since=$(date -u -d "@$(( $(date +%s) - 10 ))" +%Y-%m-%dT%H:%M:%SZ)

late() { [ "$(date +%s)" -ge "$deadline" ]; }
wait_sync() {
  local t
  until late; do
    t=$(kubectl -n kube-system logs "$kcm" --since=10s 2>/dev/null | python3 -B "$here/scripts/syncs.py" "$name" | awk -v a="$1" '($3 == "decided" || $3 == "rescaled") && $1 > a {print $1; exit}')
    [ -n "$t" ] && { echo "$t"; return 0; }
    sleep 1
  done
  return 1
}
new_pods() { kubectl get pods -l "app=$name" -o json | jq -r --arg t "$since" '.items[] | select(.metadata.creationTimestamp >= $t and .metadata.deletionTimestamp == null) | .metadata.name'; }
in_metrics() { kubectl get --raw /apis/metrics.k8s.io/v1beta1/namespaces/default/pods | jq -r '.items[].metadata.name'; }

if [ -n "$opout" ]; then
  echo "## the operation $op_ts"
  echo
  printf '%s\n' "$opout" | grep -v '^\[exit [0-9]*\]$'
  echo
fi

case $name in
  c1-1|c10)
    screen "after kubectl scale" deploy hpa describe
    manfields "after kubectl scale"
    if s=$(wait_sync "$op_ts"); then
      sleep 1
      screen "after the first HPA sync ($s)" deploy hpa describe
    else
      echo "## first HPA sync not reached before the deadline"
    fi
    pods=$(new_pods)
    tm=
    until late; do
      m=$(in_metrics)
      missing=0
      for p in $pods; do grep -qx "$p" <<< "$m" || missing=1; done
      [ -n "$pods" ] && [ "$missing" -eq 0 ] && { tm=$(ts); break; }
      sleep 1
    done
    if [ -n "$tm" ] && s=$(wait_sync "$tm"); then
      sleep 1
      screen "after the first HPA sync ($s) once the new Pods ($(echo $pods)) are in the metrics API ($tm)" deploy hpa describe
    else
      echo "## new Pods in the metrics API and the next HPA sync not reached before the deadline"
    fi
    tr=
    until late; do
      [ "$(spec_replicas "$name")" = 2 ] && { tr=$(ts); break; }
      sleep 1
    done
    if [ -n "$tr" ] && s=$(wait_sync "$tr"); then
      sleep 1
      screen "after the HPA sync ($s) that follows the revert (spec.replicas 2 seen $tr)" deploy hpa describe
      echo "\$ kubectl events --for deploy/$name"
      kubectl events --for "deploy/$name" 2>&1
      echo
      echo "\$ kubectl events --for hpa/$name"
      kubectl events --for "hpa/$name" 2>&1
      echo
      manfields "after the revert"
    else
      echo "## revert and the next HPA sync not reached before the deadline"
    fi
    ;;
  c3|c4)
    screen "after kubectl scale" deploy describe
    if s=$(wait_sync "$op_ts"); then
      sleep 1
      screen "after the first HPA sync ($s)" deploy describe
    else
      echo "## first HPA sync not reached before the deadline"
    fi
    ;;
  c7)
    manfields "after the client-side apply"
    ;;
esac
