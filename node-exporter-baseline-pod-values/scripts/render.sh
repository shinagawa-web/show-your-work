#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
cond=$1
ns=$2
set_args=()
patch='.'
case $cond in
  base) ;;
  h1) set_args=(--set hostNetwork=false) ;;
  h2) set_args=(--set hostPID=false --set service.port=9101 --set service.targetPort=9101) ;;
  r1) set_args=(--set hostRootFsMount.enabled=false --set service.port=9102 --set service.targetPort=9102) ;;
  v1) set_args=(--set hostNetwork=false --set hostPID=false --set hostRootFsMount.enabled=false) ;;
  b1) set_args=(--set hostNetwork=false --set hostPID=false --set hostRootFsMount.enabled=false) ; patch='own("proc"; "--path.procfs"; "/proc") | own("sys"; "--path.sysfs"; "/sys")' ;;
  *) echo "unknown condition $cond" >&2; exit 2 ;;
esac
"$here/.bin/helm" template "$NE_CHART" "$here/.bin/$NE_CHART-$NE_CHART_VERSION.tgz" --namespace "$ns" "${set_args[@]}" |
  kubectl create --dry-run=client -o json -f - |
  jq -s "
    def own(v; flag; to):
      .spec.template.spec.volumes |= map(select(.name != v))
      | .spec.template.spec.containers[0].volumeMounts |= map(select(.name != v))
      | .spec.template.spec.containers[0].args |= map(if startswith(flag + \"=\") then flag + \"=\" + to else . end);
    {apiVersion: \"v1\", kind: \"List\", items: map(if .kind == \"DaemonSet\" then ($patch) else . end)}
  "
