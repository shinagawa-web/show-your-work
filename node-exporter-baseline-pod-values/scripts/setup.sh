#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
bin=$here/.bin
mkdir -p "$bin"
case $(uname -m) in aarch64|arm64) arch=arm64 ;; x86_64) arch=amd64 ;; *) echo "unsupported arch" >&2; exit 1 ;; esac
if ! systemctl is-active --quiet k3s; then
  curl -fsSL "https://raw.githubusercontent.com/k3s-io/k3s/${K3S_VERSION/+/%2B}/install.sh" |
    sudo env INSTALL_K3S_VERSION="$K3S_VERSION" \
      INSTALL_K3S_EXEC="server --write-kubeconfig-mode=644 --disable=traefik,servicelb,metrics-server,local-storage --kube-proxy-arg=conntrack-max-per-core=0" \
      sh - >&2
fi
if ! "$bin/helm" version 2>/dev/null | grep -q "$HELM_VERSION"; then
  curl -fsSL "https://get.helm.sh/helm-$HELM_VERSION-linux-$arch.tar.gz" | tar -xzf - -C "$bin" --strip-components=1 "linux-$arch/helm"
fi
for c in "$NE_CHART:$NE_CHART_VERSION" "$KPS_CHART:$KPS_CHART_VERSION"; do
  [ -f "$bin/${c%%:*}-${c##*:}.tgz" ] || "$bin/helm" pull "${c%%:*}" --repo "$CHART_REPO" --version "${c##*:}" -d "$bin" >&2
done
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
for i in $(seq 90); do
  kubectl get nodes 2>/dev/null | grep -q ' Ready ' && kubectl get sa default -n default >/dev/null 2>&1 && exit 0
  sleep 1
done
echo "k3s node not ready" >&2
exit 1
