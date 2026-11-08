#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=$here/results
export PATH=$here/.bin:$PATH
KIND=v0.33.0
KUBECTL=v1.37.0
HELM=v4.3.1
JAEGER_CHART=4.16.1
COLLECTOR_CHART=0.178.2
TELEMETRYGEN=v0.165.0
arch=$(dpkg --print-architecture)

rm -rf "$results"
mkdir -p "$results" "$here/.bin"

curl -fsSLo "$here/.bin/kind" "https://kind.sigs.k8s.io/dl/$KIND/kind-linux-$arch"
curl -fsSLo "$here/.bin/kubectl" "https://dl.k8s.io/release/$KUBECTL/bin/linux/$arch/kubectl"
curl -fsSL "https://get.helm.sh/helm-$HELM-linux-$arch.tar.gz" | tar -xz -C "$here/.bin" --strip-components=1 "linux-$arch/helm"
chmod +x "$here/.bin/kind" "$here/.bin/kubectl"

latest_trace() {
  curl -fsSG 'http://localhost:16686/api/v3/traces' \
    --data-urlencode 'query.service_name=frontend' \
    --data-urlencode "query.start_time_min=$1" \
    --data-urlencode "query.start_time_max=$(date -u +%FT%TZ)" \
    --data-urlencode 'query.num_traces=1' |
    jq -c '{traceId: [.result.resourceSpans[].scopeSpans[].spans[]][0].traceId, spans: ([.result.resourceSpans[].scopeSpans[].spans[]] | length), services: ([.result.resourceSpans[].resource.attributes[] | select(.key == "service.name") | .value.stringValue] | unique)}'
}

request() {
  for i in 1 2 3; do curl -fsS 'http://localhost:8080/dispatch?customer=123' >/dev/null; done
  sleep 10
}

kind delete cluster >/dev/null 2>&1 || true
kind create cluster --wait 120s

helm repo add jaegertracing https://jaegertracing.github.io/helm-charts
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm repo update

helm install jaeger jaegertracing/jaeger --version "$JAEGER_CHART" --wait --timeout 180s
kubectl apply -f "$here/k8s/hotrod.yaml"
kubectl rollout status deployment/hotrod --timeout=180s

kubectl port-forward svc/hotrod 8080:8080 >/dev/null 2>&1 &
kubectl port-forward svc/jaeger 16686:16686 >/dev/null 2>&1 &
sleep 5

since=$(date -u +%FT%TZ)
request
curl -fsS 'http://localhost:16686/api/v3/services' | tee "$results/section2-services.json"
echo
latest_trace "$since" | tee "$results/section2-trace.json"

helm install opentelemetry-collector open-telemetry/opentelemetry-collector --version "$COLLECTOR_CHART" -f "$here/helm/collector-values.yaml" --wait --timeout 180s
kubectl get configmap opentelemetry-collector -o jsonpath='{.data.relay}' | tee "$results/section3-config.yaml"
kubectl set env deployment/hotrod OTEL_EXPORTER_OTLP_ENDPOINT=http://opentelemetry-collector:4318
kubectl rollout status deployment/hotrod --timeout=180s

pkill -f 'port-forward svc/hotrod' || true
kubectl port-forward svc/hotrod 8080:8080 >/dev/null 2>&1 &
sleep 5

since=$(date -u +%FT%TZ)
request
kubectl logs deployment/opentelemetry-collector | tee "$results/section3-collector.log" | tail -5
latest_trace "$since" | tee "$results/section3-trace.json"

helm upgrade opentelemetry-collector open-telemetry/opentelemetry-collector --version "$COLLECTOR_CHART" -f "$here/helm/collector-values.yaml" --set replicaCount=2 --wait --timeout 180s

since=$(date -u +%FT%TZ)
request
collector_pods() {
  kubectl get pods -l app.kubernetes.io/name=opentelemetry-collector --field-selector=status.phase=Running -o name | sed 's#pod/##'
}

kubectl get pods -l app.kubernetes.io/name=opentelemetry-collector | tee "$results/section4-pods.txt"
latest_trace "$since" | tee "$results/section4-trace.json"
for pod in $(collector_pods); do
  echo "$pod $(kubectl logs "$pod" --since-time="$since" | grep -c 'info	Traces')"
done | tee "$results/section4-collector-log-lines.txt"

snapshot() {
  local pod
  for pod in $(collector_pods); do
    kubectl get --raw "/api/v1/namespaces/default/pods/$pod:8888/proxy/metrics" |
      awk -v pod="$pod" '/^otelcol_receiver_accepted_spans/ { spans += $NF } END { printf "%s %d\n", pod, spans }'
  done
}

measure() {
  local replicas=$1 before after
  shift
  before=$(snapshot)
  "$@"
  sleep 10
  after=$(snapshot)
  join -a2 <(echo "$before" | sort) <(echo "$after" | sort) |
    awk -v r="$replicas" 'NF==3 { n=$3-$2 } NF==2 { n=$2 } { split($1, a, "-"); printf "| %s | %s | %d |\n", r, a[length(a)], n }'
}

load() {
  local seconds=45 w pids=()
  for w in 1 2 3 4 5 6 7 8; do
    (
      end=$((SECONDS + seconds))
      while [ "$SECONDS" -lt "$end" ]; do
        curl -s -o /dev/null 'http://localhost:8080/dispatch?customer=123' || true
      done
    ) &
    pids+=("$!")
  done
  wait "${pids[@]}"
}

telemetrygen() {
  kubectl run telemetrygen --restart=Never --image="ghcr.io/open-telemetry/opentelemetry-collector-contrib/telemetrygen:$TELEMETRYGEN" -- \
    traces --otlp-endpoint=opentelemetry-collector:4317 --otlp-insecure --workers=1 --rate=100 --duration=45s >/dev/null
  kubectl wait --for=jsonpath='{.status.phase}'=Succeeded pod/telemetrygen --timeout=180s >/dev/null
}

upgrade() {
  helm upgrade opentelemetry-collector open-telemetry/opentelemetry-collector --version "$COLLECTOR_CHART" -f "$here/helm/collector-values.yaml" --set "replicaCount=$1" --wait --timeout 180s >/dev/null 2>&1
}

upgrade 1
kubectl rollout restart deployment/hotrod >/dev/null
kubectl rollout status deployment/hotrod --timeout=180s >/dev/null
pkill -f 'port-forward svc/hotrod' || true
kubectl port-forward svc/hotrod 8080:8080 >/dev/null 2>&1 &
sleep 5

{
  echo '# otel-traces-getting-started'
  echo
  echo '## Spans received by each Collector Pod'
  echo
  echo 'For each replica count, 8 parallel `curl` loops sent requests to HotROD for 45 seconds. HotROD was not restarted between the two counts. Spans received is the increase in `otelcol_receiver_accepted_spans` on that Pod over those 45 seconds and a further 10 seconds for the last batches to arrive.'
  echo
  echo '| Collector replicas | Pod | Spans received |'
  echo '|---|---|---|'
  measure 1 load
  upgrade 2
  measure 2 load
  echo
  echo '## Spans received by each Collector Pod over one gRPC connection'
  echo
  echo 'With 2 Collector replicas, `telemetrygen` sent 100 spans per second to the Collector Service over one OTLP gRPC connection for 45 seconds. Spans received is counted the same way as above.'
  echo
  echo '| Collector replicas | Pod | Spans received |'
  echo '|---|---|---|'
  measure 2 telemetrygen
} | tee "$results/summary.md"
