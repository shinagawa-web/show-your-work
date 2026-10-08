#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
vmdir=${VMDIR:-$here/.vm}
cache=${CACHE:-$here/.cache}
cpus=${VM_CPUS:-2}
mem=${VM_MEM:-2048}
port=${VM_SSH_PORT:-2222}
mkdir -p "$vmdir" "$cache"
. "$here/scripts/versions.env"

arch=$(uname -m)
case "$arch" in
  x86_64)
    img=ubuntu-24.04-server-cloudimg-amd64.img
    img_sha256=$CLOUD_IMAGE_SHA256_AMD64
    qemu=(qemu-system-x86_64 -machine q35,accel=kvm -cpu host)
    ;;
  aarch64)
    img=ubuntu-24.04-server-cloudimg-arm64.img
    img_sha256=$CLOUD_IMAGE_SHA256_ARM64
    cp /usr/share/AAVMF/AAVMF_VARS.fd "$vmdir/efivars.fd"
    qemu=(qemu-system-aarch64 -machine virt,accel=kvm,gic-version=host -cpu host
          -drive if=pflash,format=raw,readonly=on,file=/usr/share/AAVMF/AAVMF_CODE.fd
          -drive if=pflash,format=raw,file="$vmdir/efivars.fd")
    ;;
  *) echo "unsupported arch: $arch" >&2; exit 1 ;;
esac

if [ ! -f "$cache/$img" ]; then
  curl -fsSL -o "$cache/$img.part" "https://cloud-images.ubuntu.com/releases/24.04/$CLOUD_IMAGE_RELEASE/$img"
  mv "$cache/$img.part" "$cache/$img"
fi
echo "$img_sha256  $cache/$img" | sha256sum -c --quiet - || { echo "$img does not match $CLOUD_IMAGE_RELEASE" >&2; exit 1; }

rm -f "$vmdir/disk.qcow2" "$vmdir/seed.img" "$vmdir/id" "$vmdir/id.pub"
qemu-img create -q -f qcow2 -F qcow2 -b "$cache/$img" "$vmdir/disk.qcow2" 10G
ssh-keygen -q -t ed25519 -N '' -f "$vmdir/id"
cat > "$vmdir/user-data" <<UD
#cloud-config
users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - $(cat "$vmdir/id.pub")
UD
printf 'instance-id: testvm\nlocal-hostname: testvm\n' > "$vmdir/meta-data"
cloud-localds "$vmdir/seed.img" "$vmdir/user-data" "$vmdir/meta-data"

{ "${qemu[0]}" --version | head -1; printf '%q ' "${qemu[@]}" -smp "$cpus" -m "$mem"; echo; } > "$vmdir/qemu.txt"
t0=$(date +%s.%N)
echo "$t0" > "$vmdir/t0"
"${qemu[@]}" -smp "$cpus" -m "$mem" \
  -drive if=virtio,format=qcow2,file="$vmdir/disk.qcow2" \
  -drive if=virtio,format=raw,file="$vmdir/seed.img" \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:"$port"-:22 -device virtio-net-pci,netdev=n0,romfile= \
  -display none -serial file:"$vmdir/serial.log" \
  -pidfile "$vmdir/qemu.pid" -daemonize

ssh_opts=(-i "$vmdir/id" -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o ConnectTimeout=3 -o LogLevel=ERROR -o BatchMode=yes)
deadline=$(( $(date +%s) + ${VM_BOOT_TIMEOUT:-240} ))
until ssh "${ssh_opts[@]}" ubuntu@127.0.0.1 true 2>/dev/null; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "test VM did not accept SSH within ${VM_BOOT_TIMEOUT:-240}s" >&2
    tail -50 "$vmdir/serial.log" >&2
    exit 1
  fi
  sleep 1
done
t1=$(date +%s.%N)
awk -v a="$t1" -v b="$t0" 'BEGIN{printf "ssh_ready_seconds=%.1f\n", a-b}'
