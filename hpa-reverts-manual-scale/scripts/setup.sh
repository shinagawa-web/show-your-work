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
ms=$bin/metrics-server-$METRICS_SERVER_VERSION.yaml
if ! echo "$METRICS_SERVER_MANIFEST_SHA256  $ms" | sha256sum -c --status 2>/dev/null; then
  curl -fsSLo "$ms" "https://github.com/kubernetes-sigs/metrics-server/releases/download/$METRICS_SERVER_VERSION/components.yaml"
  echo "$METRICS_SERVER_MANIFEST_SHA256  $ms" | sha256sum -c >&2
fi
docker pull -q "busybox@$BUSYBOX_DIGEST" >&2
docker tag "busybox@$BUSYBOX_DIGEST" "$BUSYBOX_TAG"
"$bin/kind" delete cluster --name hpa >&2 2>/dev/null || true
"$bin/kind" create cluster --name hpa --image "$KIND_NODE_IMAGE" --config "$here/k8s/kind.yaml" --wait 120s >&2
docker save "$BUSYBOX_TAG" | docker exec -i hpa-control-plane ctr -n k8s.io images import - >&2
