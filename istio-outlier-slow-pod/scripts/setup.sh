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
if ! "$bin/istioctl" version --remote=false 2>/dev/null | grep -q "$ISTIO_VERSION"; then
  up=$(echo "$arch" | tr a-z A-Z)
  sum=ISTIOCTL_SHA256_$up
  tarball=$bin/istioctl.tar.gz
  curl -fsSLo "$tarball" "https://github.com/istio/istio/releases/download/$ISTIO_VERSION/istioctl-$ISTIO_VERSION-linux-$arch.tar.gz"
  echo "${!sum}  $tarball" | sha256sum -c >&2
  tar -C "$bin" -xzf "$tarball" istioctl
  rm "$tarball"
fi
"$bin/kind" delete cluster --name "$CLUSTER" >&2 2>/dev/null || true
"$bin/kind" create cluster --name "$CLUSTER" --image "$KIND_NODE_IMAGE" --config "$here/k8s/kind.yaml" --wait 120s >&2 &
create=$!
docker build -q --build-arg GO_IMAGE="$GO_IMAGE" -t "$APP_TAG" "$here/app" >&2
docker pull -q "grafana/k6@$K6_DIGEST" >&2
docker tag "grafana/k6@$K6_DIGEST" "$K6_TAG"
docker pull -q "docker.io/istio/pilot@$PILOT_DIGEST" >&2
docker tag "docker.io/istio/pilot@$PILOT_DIGEST" "docker.io/istio/pilot:$ISTIO_VERSION"
docker pull -q "docker.io/istio/proxyv2@$PROXYV2_DIGEST" >&2
docker tag "docker.io/istio/proxyv2@$PROXYV2_DIGEST" "docker.io/istio/proxyv2:$ISTIO_VERSION"
wait "$create"
docker save "$APP_TAG" "$K6_TAG" "docker.io/istio/pilot:$ISTIO_VERSION" "docker.io/istio/proxyv2:$ISTIO_VERSION" | docker exec -i "$CLUSTER-control-plane" ctr -n k8s.io images import - >&2
"$bin/istioctl" install -y --set profile=minimal --set meshConfig.accessLogFile=/dev/stdout --set meshConfig.accessLogEncoding=JSON --set values.global.proxy.holdApplicationUntilProxyStarts=true --readiness-timeout 180s >&2
