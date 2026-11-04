#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
bin=$here/.bin
mkdir -p "$bin"
case $(uname -m) in
  aarch64|arm64) arch=arm64 helm_sha=$HELM_SHA256_ARM64 ;;
  x86_64) arch=amd64 helm_sha=$HELM_SHA256_AMD64 ;;
  *) echo "unsupported arch" >&2; exit 1 ;;
esac
if ! "$bin/kind" version 2>/dev/null | grep -q "$KIND_VERSION"; then
  curl -fsSLo "$bin/kind" "https://kind.sigs.k8s.io/dl/$KIND_VERSION/kind-linux-$arch"
  chmod +x "$bin/kind"
fi
if ! "$bin/kubectl" version --client 2>/dev/null | grep -q "$KUBECTL_VERSION"; then
  curl -fsSLo "$bin/kubectl" "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/$arch/kubectl"
  chmod +x "$bin/kubectl"
fi
if ! "$bin/helm" version --short 2>/dev/null | grep -q "$HELM_VERSION"; then
  tgz=$(mktemp)
  curl -fsSLo "$tgz" "https://get.helm.sh/helm-$HELM_VERSION-linux-$arch.tar.gz"
  echo "$helm_sha  $tgz" | sha256sum -c -
  tar -xzf "$tgz" -C "$bin" --strip-components=1 "linux-$arch/helm"
  rm -f "$tgz"
fi
chart=$bin/redis-ha-$REDIS_HA_CHART.tgz
if [ ! -f "$chart" ]; then
  curl -fsSLo "$chart" "https://github.com/DandyDeveloper/charts/releases/download/redis-ha-$REDIS_HA_CHART/redis-ha-$REDIS_HA_CHART.tgz"
fi
echo "$REDIS_HA_CHART_SHA256  $chart" | sha256sum -c -
if [ "${REUSE:-}" = yes ] && "$bin/kind" get clusters 2>/dev/null | grep -qx "$CLUSTER" && docker exec "$PG" pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1; then
  "$bin/helm" uninstall redis-ha >/dev/null 2>&1 || true
  "$bin/kubectl" delete deploy,sts,job,configmap --all --wait=false >/dev/null
  "$bin/kubectl" delete pod --all --force --grace-period=0 >/dev/null 2>&1
  "$bin/kubectl" delete pvc --all --timeout=60s >/dev/null
  docker exec "$CLUSTER-control-plane" sh -c 'rm -rf /k6out /stats && mkdir -m 777 /k6out /stats'
  exit 0
fi
docker rm -fv "$PG" >/dev/null 2>&1 || true
"$bin/kind" delete cluster --name "$CLUSTER" 2>/dev/null || true
docker volume prune -f >/dev/null
"$bin/kind" create cluster --name "$CLUSTER" --image "$KIND_NODE_IMAGE" --config "$here/k8s/kind.yaml" --wait 120s &
create=$!
docker build -q --build-arg GO_IMAGE="$GO_IMAGE" --build-arg BASE_IMAGE="$BASE_IMAGE" -t "$APP_IMAGE" "$here/app" &
build=$!
docker pull -q "grafana/k6@$K6_DIGEST"
docker tag "grafana/k6@$K6_DIGEST" "$K6_TAG"
docker pull -q "$POSTGRES_IMAGE"
images=("$APP_IMAGE" "$K6_TAG")
if [ "${REUSE:-}" = first ] || [ "${REDIS#single}" != "$REDIS" ]; then
  docker pull -q "$REDIS_IMAGE"; docker tag "$REDIS_IMAGE" "$REDIS_TAG"; images+=("$REDIS_TAG")
fi
if [ "${REUSE:-}" = first ] || [ "$REDIS" = sentinel ]; then
  docker pull -q "$REDIS_HA_IMAGE"; docker tag "$REDIS_HA_IMAGE" "$REDIS_HA_TAG"; images+=("$REDIS_HA_TAG")
fi
wait "$create"
docker update --cpuset-cpus "$NODE_CPUS" "$CLUSTER-control-plane"
docker run -d --name "$PG" --cpuset-cpus "$PG_CPUS" --network kind -e POSTGRES_PASSWORD=pg "$POSTGRES_IMAGE"
wait "$build"
for img in "${images[@]}"; do
  docker save "$img" | docker exec -i "$CLUSTER-control-plane" ctr -n k8s.io images import -
done
docker exec "$CLUSTER-control-plane" sh -c 'rm -rf /k6out /stats && mkdir -m 777 /k6out /stats'
