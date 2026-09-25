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
docker build -q -t distroless-app:dev "$here/app" >&2
docker pull -q "nicolaka/netshoot@$NETSHOOT_DIGEST" >&2
docker tag "nicolaka/netshoot@$NETSHOOT_DIGEST" "$NETSHOOT_TAG"
if ! "$bin/kind" get clusters 2>/dev/null | grep -qx ded; then
  "$bin/kind" create cluster --name ded --image "$KIND_NODE_IMAGE" --wait 120s >&2
fi
for img in distroless-app:dev "$NETSHOOT_TAG"; do
  docker save "$img" | docker exec -i ded-control-plane ctr -n k8s.io images import - >&2
done
