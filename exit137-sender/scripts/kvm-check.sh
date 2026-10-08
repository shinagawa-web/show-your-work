#!/usr/bin/env bash
set -uo pipefail
echo "\$ uname -a"; uname -a
echo "\$ ls -l /dev/kvm"; ls -l /dev/kvm 2>&1
echo "\$ dmesg | grep -i kvm"; sudo dmesg 2>/dev/null | grep -i kvm | head -20
python3 - <<'PY'
import fcntl, os, sys
KVM_GET_API_VERSION = 0xAE00
KVM_CREATE_VM = 0xAE01
try:
    fd = os.open("/dev/kvm", os.O_RDWR | os.O_CLOEXEC)
except OSError as e:
    print(f"open /dev/kvm: {e}")
    sys.exit(1)
print(f"KVM_GET_API_VERSION = {fcntl.ioctl(fd, KVM_GET_API_VERSION, 0)}")
try:
    vm = fcntl.ioctl(fd, KVM_CREATE_VM, 0)
    print(f"KVM_CREATE_VM = fd {vm}")
    os.close(vm)
except OSError as e:
    print(f"KVM_CREATE_VM: {e}")
    sys.exit(1)
PY
