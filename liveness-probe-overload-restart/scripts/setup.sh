#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
bin=$here/.bin
mkdir -p "$bin"
case $(uname -m) in aarch64|arm64) arch=arm64 ;; x86_64) arch=amd64 ;; *) echo "unsupported arch" >&2; exit 1 ;; esac
if ! "$bin/kind" version 2>/dev/null | grep -q "$KIND_VERSION"; then
  curl -fsSLo "$bin/kind" "https://kind.sigs.k8s.io/dl/$KIND_VERSION/kind-linux-$arch"
  chmod +x "$bin/kind"
fi
if ! "$bin/kubectl" version --client 2>/dev/null | grep -q "$KUBECTL_VERSION"; then
  curl -fsSLo "$bin/kubectl" "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/$arch/kubectl"
  chmod +x "$bin/kubectl"
fi
"$bin/kind" delete cluster --name "$CLUSTER" >&2 2>/dev/null || true
"$bin/kind" create cluster --name "$CLUSTER" --image "$KIND_NODE_IMAGE" --config "$here/k8s/kind.yaml" --wait 120s >&2 &
create=$!
docker build -q --build-arg PYTHON_IMAGE="$PYTHON_IMAGE" --build-arg GUNICORN_VERSION="$GUNICORN_VERSION" --build-arg GUNICORN_SHA256="$GUNICORN_SHA256" -t "$APP_IMAGE" "$here/app" >&2
docker pull -q "grafana/k6@$K6_DIGEST" >&2
docker tag "grafana/k6@$K6_DIGEST" "$K6_TAG"
wait "$create"
docker save "$APP_IMAGE" | docker exec -i "$CLUSTER-worker" ctr -n k8s.io images import - >&2
docker save "$K6_TAG" | docker exec -i "$CLUSTER-control-plane" ctr -n k8s.io images import - >&2
"$bin/kubectl" taint nodes "$CLUSTER-control-plane" node-role.kubernetes.io/control-plane- >&2
docker exec "$CLUSTER-control-plane" sh -c 'rm -rf /k6out && mkdir -m 777 /k6out'
