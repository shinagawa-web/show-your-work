#!/usr/bin/env bash
set -uo pipefail
cond=$1
out=$2
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
. "$here/scripts/params.env"
export PATH=$here/.bin:$PATH
mkdir -p "$out"
fqdn=app.demo.svc.cluster.local
cluster="outbound|8080||$fqdn"
abort() { echo "$1" > "$out/invalid"; echo "RUN INVALID: $cond: $1" >&2; exit 1; }

bad_status=200
bad_delay=$SLOW_MS
bad_body_delay=0
max=34
route_timeout=
client_timeout=
quiet=0
case $cond in
  5xx-default) bad_status=500; bad_delay=$NORMAL_MS; max= ;;
  5xx-34) bad_status=500; bad_delay=$NORMAL_MS ;;
  slow-34) ;;
  slow-client-200ms) client_timeout=200ms ;;
  slow-body-200ms) bad_delay=0; bad_body_delay=$SLOW_MS; route_timeout=200ms ;;
  route-200ms) route_timeout=200ms; quiet=$QUIET ;;
  route-1000ms) route_timeout=1000ms; quiet=$QUIET ;;
  *) abort "unknown condition" ;;
esac

kubectl delete namespace demo --ignore-not-found --wait=true >/dev/null 2>&1
kubectl create namespace demo >/dev/null || abort "namespace"
kubectl label namespace demo istio-injection=enabled >/dev/null || abort "namespace label"
sed -e "s|@APP_TAG@|$APP_TAG|g" -e "s|@NORMAL_MS@|$NORMAL_MS|g" -e "s|@BAD_DELAY_MS@|$bad_delay|g" -e "s|@BAD_STATUS@|$bad_status|g" -e "s|@BAD_BODY_DELAY_MS@|$bad_body_delay|g" \
  "$here/k8s/app.yaml" | kubectl apply -f - >/dev/null || abort "app"
if [ -n "$max" ]; then
  sed -e "s|@MAX_EJECTION@|$max|g" "$here/k8s/destinationrule.yaml" | kubectl apply -f - >/dev/null || abort "destinationrule"
else
  sed -e '/@MAX_EJECTION@/d' "$here/k8s/destinationrule.yaml" | kubectl apply -f - >/dev/null || abort "destinationrule"
fi
if [ -n "$route_timeout" ]; then
  sed -e "s|@TIMEOUT@|$route_timeout|g" "$here/k8s/virtualservice.yaml" | kubectl apply -f - >/dev/null || abort "virtualservice"
fi
kubectl -n demo create configmap k6-script --from-file="$here/k6/load.js" >/dev/null || abort "configmap"
sed -e "s|@K6_TAG@|$K6_TAG|g" "$here/k8s/k6.yaml" | kubectl apply -f - >/dev/null || abort "k6 pod"
kubectl -n demo rollout status deploy/app-normal --timeout=90s >/dev/null || abort "app-normal rollout"
kubectl -n demo rollout status deploy/app-bad --timeout=90s >/dev/null || abort "app-bad rollout"
kubectl -n demo wait pod/k6 --for=condition=Ready --timeout=90s >/dev/null || abort "k6 not ready"

expect_max=${max:-none}
python3 -B "$here/scripts/preflight.py" "$out" "$cluster" "$fqdn" "$expect_max" "${route_timeout:-none}" || abort "preflight: $(cat "$out/preflight.err" 2>/dev/null)"

kubectl -n demo get pods -l app=app -o json > "$out/pods.json"
sleep "$quiet"

(
  sleep "$MID"
  istioctl proxy-config endpoint k6.demo --cluster "$cluster" > "$out/endpoint-mid.txt" 2>&1
) &
mid=$!
kubectl -n demo exec k6 -c k6 -- env RATE="$RATE" WINDOW="$WINDOW" K6_TIMEOUT="${client_timeout:-60s}" \
  k6 run --quiet --no-usage-report --out json=/out/k6.json /scripts/load.js > "$out/k6.log" 2>&1
k6rc=$?
wait "$mid"
[ "$k6rc" = 0 ] || abort "k6 exited $k6rc"

kubectl -n demo exec k6 -c k6 -- cat /out/k6.json > "$out/k6.json" || abort "copy k6.json"
kubectl -n demo exec k6 -c istio-proxy -- pilot-agent request GET "stats?filter=app%5C.demo.*outlier_detection" > "$out/stats-end.txt" 2>&1
sleep 3
kubectl -n demo logs k6 -c istio-proxy > "$out/proxy.log" 2>&1

cat > "$out/cond.env" <<V
bad_status=$bad_status
bad_body_delay=$bad_body_delay
max=${max:-default}
route_timeout=${route_timeout:-none}
client_timeout=${client_timeout:-none}
V
python3 -B "$here/scripts/measure.py" "$out" "$RATE" "$WINDOW" > "$out/metrics.json" || abort "measure.py"
