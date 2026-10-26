#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
. "$here/scripts/versions.env"
export PATH=$here/.bin:$PATH
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
SECTIONS=${*:-s1 s2 s3 s4}
SEL=app.kubernetes.io/name=prometheus-node-exporter
HELD_CONNECTIONS=10000
CONNTRACK_RATIO_PERCENT=85
ALERT_THRESHOLD_PERCENT=75
start=$(date +%s)
holder=
max0=
invalid=()
rm -rf "$results"
mkdir -p "$results/metrics"
abort() { echo "RUN INVALID: $1" | tee "$results/summary.md"; exit 1; }
has() { [[ " $SECTIONS " == *" $1 "* ]]; }

for s in $SECTIONS; do
  case $s in s1|s2|s3|s4) ;; *) abort "unknown section $s (expected s1 s2 s3 s4)" ;; esac
done
conds=()
has s1 && conds+=(base)
has s3 && conds+=(base h1 r1 v1 b1)
has s2 && conds+=(base h1 h2 r1 v1 b1)
CONDS=$(for c in base h1 h2 r1 v1 b1; do [[ " ${conds[*]-} " == *" $c "* ]] && echo "$c"; done | paste -sd' ' -)

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
elapsed() { echo $(( $(date +%s) - start )); }
cells() { sudo env PATH="$PATH" KUBECONFIG="$KUBECONFIG" python3 -B "$here/scripts/cells.py" "$@"; }
cleanup() {
  [ -n "$max0" ] && sudo sysctl -qw net.netfilter.nf_conntrack_max="$max0"
  [ -n "$holder" ] && sudo kill "$holder" 2>/dev/null
}
trap cleanup EXIT

install_kps() {
  r kubectl create ns monitoring-ne
  r kubectl label --overwrite ns monitoring-ne pod-security.kubernetes.io/enforce=privileged
  r kubectl create ns monitoring
  r helm install kps "$here/.bin/$KPS_CHART-$KPS_CHART_VERSION.tgz" -n monitoring \
    --set prometheus-node-exporter.namespaceOverride=monitoring-ne \
    --set prometheus-node-exporter.service.port=9103 \
    --set prometheus-node-exporter.service.targetPort=9103
}

"$here/scripts/setup.sh" || abort "setup.sh failed"
setup_seconds=$(elapsed)

if has s2; then
  sudo python3 -B "$here/scripts/holder.py" "$HELD_CONNECTIONS" "$results/.holder-ready" &
  holder=$!
  sed "s|@PROMETHEUS_IMAGE@|$PROMETHEUS_IMAGE|g" "$here/k8s/prometheus.yaml" | kubectl apply -f - > "$results/deploy-prometheus.txt" 2>&1 || abort "kubectl apply of k8s/prometheus.yaml failed"
fi
for c in $CONDS; do
  kubectl create ns "ne-$c" >/dev/null 2>&1
  "$here/scripts/render.sh" "$c" "ne-$c" | kubectl apply -f - > "$results/deploy-$c.txt" 2>&1 || abort "apply of variant $c failed"
done
if has s4; then
  install_kps > "$results/countermeasure-install.txt" 2>&1 &
  kps=$!
fi

for c in $CONDS; do
  kubectl rollout status ds/prometheus-node-exporter -n "ne-$c" --timeout=150s >/dev/null || abort "DaemonSet of variant $c did not roll out"
done
if has s2; then
  kubectl rollout status deploy/prometheus -n prom --timeout=150s >/dev/null || abort "Prometheus did not roll out"
  for i in $(seq 60); do
    up=$(cells query 'sum(up{job="node-exporter"})' 2>/dev/null | awk 'NR==2 {print $NF}')
    [ "$up" = "6" ] && break
    sleep 2
  done
  [ "$up" = "6" ] || abort "Prometheus does not see the 6 node-exporter targets up (got ${up:-none})"
  for i in $(seq 30); do [ -s "$results/.holder-ready" ] && break; sleep 1; done
  [ -s "$results/.holder-ready" ] || abort "scripts/holder.py did not open its connections"
fi
ready_seconds=$(elapsed)

{
  r uname -r
  r k3s --version
  r kubectl version
  r helm version
  rs "helm show chart $here/.bin/$NE_CHART-$NE_CHART_VERSION.tgz | grep -E '^(name|version|appVersion):'"
  rs "helm show chart $here/.bin/$KPS_CHART-$KPS_CHART_VERSION.tgz | grep -E '^(name|version|appVersion):'"
  if [ -n "$CONDS" ]; then
    rs "kubectl get pods -n ne-base -l $SEL -o jsonpath='{.items[0].spec.containers[0].image}{\"\\n\"}'"
    rs "kubectl get pods -n ne-base -l $SEL -o jsonpath='{.items[0].status.containerStatuses[0].imageID}{\"\\n\"}'"
  fi
  has s2 && rs "kubectl get pods -n prom -l app=prometheus -o jsonpath='{.items[0].status.containerStatuses[0].imageID}{\"\\n\"}'"
  r kubectl get nodes -o wide
  r kubectl get pods -A -o wide
  rs "grep -o 'conntrack-max-per-core=[0-9]*' /etc/systemd/system/k3s.service"
} > "$results/00-environment.txt"
{
  echo "kernel=$(uname -r)"
  echo "k3s=$(k3s --version | head -n1)"
  echo "kubernetes=$(kubectl version -o json | jq -r .serverVersion.gitVersion)"
  echo "helm=$(helm version --template '{{.Version}}')"
  echo "prometheus-node-exporter chart=$NE_CHART_VERSION (appVersion $(helm show chart "$here/.bin/$NE_CHART-$NE_CHART_VERSION.tgz" | awk '/^appVersion:/ {print $2}'))"
  [ -n "$CONDS" ] && echo "node-exporter image=$(kubectl get pods -n ne-base -l "$SEL" -o jsonpath='{.items[0].spec.containers[0].image}')"
  has s4 && echo "kube-prometheus-stack chart=$KPS_CHART_VERSION"
} > "$results/versions.txt"

if has s1 || has s2; then
  for c in $CONDS; do
    has s2 || [ "$c" = base ] || continue
    echo "## $c"
    r kubectl get ds prometheus-node-exporter -n "ne-$c" -o jsonpath='{.spec.template.spec.hostNetwork} {.spec.template.spec.hostPID} {.spec.template.spec.volumes[*].hostPath.path} {.spec.template.spec.containers[0].args}{"\n"}'
    r kubectl get pods -n "ne-$c" -l "$SEL" -o jsonpath='{.items[0].spec.containers[0].ports}{"\n"}'
    r kubectl label --dry-run=server --overwrite ns "ne-$c" pod-security.kubernetes.io/enforce=baseline
  done > "$results/dryrun.txt" 2>&1
fi

if has s3; then
  for c in base h1 r1 v1 b1; do
    r kubectl get daemonset -n "ne-$c" prometheus-node-exporter -o yaml > "$results/daemonset-$c.yaml"
  done
fi

if has s2; then
  max0=$(cat /proc/sys/net/netfilter/nf_conntrack_max)
  count=$(cat /proc/sys/net/netfilter/nf_conntrack_count)
  {
    echo "holder: $(cat "$results/.holder-ready")"
    echo "node nf_conntrack_max before: $max0"
    echo "node nf_conntrack_count with held connections: $count"
    r sudo sysctl -w net.netfilter.nf_conntrack_max=$(( count * 100 / CONNTRACK_RATIO_PERCENT ))
    echo "node nf_conntrack_count after lowering: $(cat /proc/sys/net/netfilter/nf_conntrack_count)"
  } > "$results/conntrack.txt" 2>&1
  sleep 12
  count=$(cat /proc/sys/net/netfilter/nf_conntrack_count)
  max=$(cat /proc/sys/net/netfilter/nf_conntrack_max)
  {
    echo "node nf_conntrack_count=$count nf_conntrack_max=$max"
    cells alert $CONDS
  } > "$results/alert.txt" 2>&1 || invalid+=("s2: the alert expression could not be queried (alert.txt)")
  [ $(( count * 100 )) -gt $(( max * ALERT_THRESHOLD_PERCENT )) ] || invalid+=("s2: node conntrack count $count of $max was not above the alert threshold when the expression was evaluated")
  cells cells "$results/metrics" $CONDS > "$results/cells.txt" 2>&1 || invalid+=("s2: scripts/cells.py cells failed (cells.txt)")
  cells labels $CONDS > "$results/labels.txt" 2>&1 || invalid+=("s2: scripts/cells.py labels failed (labels.txt)")
  {
    echo "node nf_conntrack_count before restoring: $(cat /proc/sys/net/netfilter/nf_conntrack_count)"
    r sudo sysctl -w net.netfilter.nf_conntrack_max="$max0"
    rs "sudo dmesg | grep -c 'nf_conntrack: table full' || true"
  } >> "$results/conntrack.txt" 2>&1
  max0=
  sudo dmesg | grep -q 'nf_conntrack: table full' && invalid+=("s2: the node conntrack table overflowed during the run")
fi

if has s4; then
  wait "$kps" || invalid+=("s4: the kube-prometheus-stack install step failed (countermeasure-install.txt)")
  grep -q 'STATUS: deployed' "$results/countermeasure-install.txt" || invalid+=("s4: kube-prometheus-stack was not installed (countermeasure-install.txt)")
  for i in $(seq 150); do
    kubectl get pods -n monitoring -o name 2>/dev/null | grep -q '^pod/prometheus-kps-' &&
      kubectl get pods -n monitoring-ne -o name 2>/dev/null | grep -q node-exporter && break
    sleep 2
  done
  kubectl get pods -n monitoring -o name | grep -q '^pod/prometheus-kps-' || invalid+=("s4: no kube-prometheus-stack Prometheus Pod in monitoring before the dry-run")
  kubectl get pods -n monitoring-ne -o name | grep -q node-exporter || invalid+=("s4: no node-exporter Pod in monitoring-ne before the dry-run")
  {
    r helm list -n monitoring
    r kubectl get pods -n monitoring -o wide
    r kubectl get pods -n monitoring-ne -o wide
    r kubectl get ns monitoring monitoring-ne --show-labels
    r kubectl label --dry-run=server --overwrite ns monitoring pod-security.kubernetes.io/enforce=baseline
  } > "$results/countermeasure.txt" 2>&1
fi

echo "sections=$SECTIONS setup_seconds=$setup_seconds ready_seconds=$ready_seconds elapsed_seconds=$(elapsed)" > "$results/elapsed.txt"
python3 -B "$here/scripts/summary.py" "$results" "$SECTIONS" ${invalid[@]+"${invalid[@]}"} > "$results/summary.md" || abort "summary.py failed"
cat "$results/summary.md"
! grep -q '^RUN INVALID' "$results/summary.md"
