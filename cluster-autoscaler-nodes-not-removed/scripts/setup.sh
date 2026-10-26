#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
bin=$here/.bin
mkdir -p "$bin"
case $(uname -m) in aarch64|arm64) arch=arm64 ;; x86_64) arch=amd64 ;; *) echo "unsupported arch" >&2; exit 1 ;; esac
fetch() {
  local url=$1 sum=$2 dest=$3
  if ! echo "$sum  $dest" | sha256sum -c --status 2>/dev/null; then
    curl -fsSLo "$dest" "$url"
    echo "$sum  $dest" | sha256sum -c >&2
  fi
}
up=$(echo "$arch" | tr a-z A-Z)
kind_sum=KIND_SHA256_$up
kubectl_sum=KUBECTL_SHA256_$up
fetch "https://kind.sigs.k8s.io/dl/$KIND_VERSION/kind-linux-$arch" "${!kind_sum}" "$bin/kind"
fetch "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/$arch/kubectl" "${!kubectl_sum}" "$bin/kubectl"
chmod +x "$bin/kind" "$bin/kubectl"
fetch "https://github.com/kubernetes-sigs/kwok/releases/download/$KWOK_VERSION/kwok.yaml" "$KWOK_MANIFEST_SHA256" "$bin/kwok-$KWOK_VERSION.yaml"
fetch "https://github.com/kubernetes-sigs/kwok/releases/download/$KWOK_VERSION/stage-fast.yaml" "$KWOK_STAGE_FAST_SHA256" "$bin/stage-fast-$KWOK_VERSION.yaml"
"$bin/kind" delete cluster --name ca >&2 2>/dev/null || true
"$bin/kind" create cluster --name ca --image "$KIND_NODE_IMAGE" --config "$here/k8s/kind.yaml" --wait 120s >&2
