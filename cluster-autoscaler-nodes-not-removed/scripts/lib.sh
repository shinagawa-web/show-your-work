ts() { date -u +%H:%M:%S.%6N; }
mark() { echo "$(ts) $*" >> "$out/events.log"; }
render() {
  sed -e "s|@PAUSE_IMAGE@|$PAUSE_IMAGE|g" -e "s|@KSM_IMAGE@|$KSM_IMAGE|g" -e "s|@MIN_COUNT@|${MIN_COUNT:-0}|g" "$@"
}
ksm_metrics() {
  kubectl get --raw "/api/v1/namespaces/kube-system/services/kube-state-metrics:http-metrics/proxy/metrics" > "$1"
}
snapshot() {
  local name=$1
  ksm_metrics "$out/metrics-$name.txt" || return 1
  python3 -B "$here/scripts/promql.py" < "$out/metrics-$name.txt" > "$out/promql-$name.txt" || return 1
  kubectl get pods -A -o wide --sort-by=.spec.nodeName > "$out/pods-$name.txt" 2>&1
  mark "snapshot $name"
}
node_names() { kubectl get nodes -l type=kwok -o jsonpath='{range .items[*]}{.metadata.name}{" "}{end}'; }
lower_apps() {
  local d
  for d in web api worker; do
    python3 -B "$here/scripts/apps.py" "$1" "$2" "${SIDECAR:-0}" "$d" "$PAUSE_IMAGE" | kubectl apply -f - >/dev/null || return 1
    kubectl rollout status deploy/$d --timeout=120s >/dev/null || return 1
  done
}
