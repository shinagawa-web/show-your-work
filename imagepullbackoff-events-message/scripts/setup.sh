#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
bin=$here/.bin
mkdir -p "$bin"
m=$(uname -m)
case $m in aarch64|arm64) arch=arm64; carch=arm64 ;; x86_64) arch=amd64; carch=x86_64 ;; *) echo "unsupported arch $m" >&2; exit 1 ;; esac
if ! "$bin/kind" version 2>/dev/null | grep -q "$KIND_VERSION"; then
  curl -fsSLo "$bin/kind" "https://kind.sigs.k8s.io/dl/$KIND_VERSION/kind-linux-$arch"
  chmod +x "$bin/kind"
fi
if ! "$bin/kubectl" version --client 2>/dev/null | grep -q "$KUBECTL_VERSION"; then
  curl -fsSLo "$bin/kubectl" "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/$arch/kubectl"
  chmod +x "$bin/kubectl"
fi
if ! "$bin/crane" version 2>/dev/null | grep -q "${CRANE_VERSION#v}"; then
  sum_var=CRANE_SHA256_$carch
  tgz=$bin/crane.tar.gz
  curl -fsSLo "$tgz" "https://github.com/google/go-containerregistry/releases/download/$CRANE_VERSION/go-containerregistry_Linux_$carch.tar.gz"
  echo "${!sum_var}  $tgz" | sha256sum -c >&2
  tar -C "$bin" -xzf "$tgz" crane
  rm -f "$tgz"
fi
command -v htpasswd >/dev/null || { echo "htpasswd not found (apache2-utils)" >&2; exit 1; }
docker pull -q "$KIND_NODE_IMAGE" >&2
docker pull -q "$REGISTRY_IMAGE" >&2
