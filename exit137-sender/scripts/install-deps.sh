#!/usr/bin/env bash
set -euo pipefail
arch=$(uname -m)
case "$arch" in
  x86_64)  pkgs="qemu-system-x86 qemu-utils" ;;
  aarch64) pkgs="qemu-system-arm qemu-efi-aarch64 qemu-utils" ;;
  *) echo "unsupported arch: $arch" >&2; exit 1 ;;
esac
sudo apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
  $pkgs cloud-image-utils openssh-client curl python3 gcc libc6-dev busybox-static
if [ -e /dev/kvm ]; then
  echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' \
    | sudo tee /etc/udev/rules.d/99-kvm4all.rules >/dev/null
  sudo udevadm control --reload-rules
  sudo udevadm trigger --name-match=kvm
fi
