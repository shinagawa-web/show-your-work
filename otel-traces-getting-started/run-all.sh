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
  for i in $(seq "$1"); do curl -fsS 'http://localhost:8080/dispatch?customer=123' >/dev/null; done
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
request 3
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
request 3
kubectl logs deployment/opentelemetry-collector | tee "$results/section3-collector.log" | tail -5
latest_trace "$since" | tee "$results/section3-trace.json"

helm upgrade opentelemetry-collector open-telemetry/opentelemetry-collector --version "$COLLECTOR_CHART" -f "$here/helm/collector-values.yaml" --set replicaCount=2 --wait --timeout 180s

since=$(date -u +%FT%TZ)
request 3
kubectl get pods -l app.kubernetes.io/name=opentelemetry-collector | tee "$results/section4-pods.txt"
latest_trace "$since" | tee "$results/section4-trace.json"

{
  echo '# otel-traces-getting-started'
  echo
  echo '## Collector Pods after replicaCount=2'
  echo
  echo 'Output of `kubectl get pods` for the Collector after `helm upgrade --set replicaCount=2`.'
  echo
  echo '| Pod | Ready | Status |'
  echo '|---|---|---|'
  awk 'NR > 1 { n = split($1, a, "-"); printf "| %s | %s | %s |\n", a[n], $2, $3 }' "$results/section4-pods.txt"
  echo
  echo '## Latest frontend trace after replicaCount=2'
  echo
  echo 'The latest `frontend` trace in Jaeger after 3 requests were sent to HotROD with 2 Collector replicas.'
  echo
  echo '| Spans | Services |'
  echo '|--:|---|'
  jq -r '"| \(.spans) | \(.services | join(", ")) |"' "$results/section4-trace.json"
} | tee "$results/summary.md"
