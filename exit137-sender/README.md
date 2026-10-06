# exit137-sender

Preflight for this directory: check that a test VM can be started with QEMU+KVM on the Linux layer, locally and on CI, with the same scripts.

| Layer | Local | CI |
|---|---|---|
| Linux | lima VM (vz, aarch64) on macOS | GitHub Actions `ubuntu-24.04` runner (x86_64) |
| Test VM | QEMU+KVM inside the lima VM | QEMU+KVM on the runner |

## Run

On the CI runner, or any Ubuntu 24.04 host with `/dev/kvm`:

```
cd exit137-sender
./kvm-preflight.sh
```

Locally on Apple Silicon (M3 or later, macOS 15 or later), start the Linux layer with nested virtualization and run the same script inside it:

```
limactl start --name=exit137 --tty=false exit137-sender/lima.yaml
limactl copy -r exit137-sender exit137:/tmp/
limactl shell exit137 -- /tmp/exit137-sender/kvm-preflight.sh
```

Raw output goes under `results/preflight/`.

## Scripts

- `scripts/install-deps.sh` installs QEMU, firmware and cloud-image-utils for the host architecture and makes `/dev/kvm` accessible to the current user
- `scripts/kvm-check.sh` prints `/dev/kvm` and the kernel's KVM messages, then calls `KVM_GET_API_VERSION` and `KVM_CREATE_VM`
- `scripts/vm-up.sh` boots the Ubuntu 24.04 cloud image with QEMU+KVM and a cloud-init seed, and waits until a command runs over SSH
- `scripts/vm-ssh.sh` runs a command in the test VM
- `scripts/vm-down.sh` stops the test VM

`vm-up.sh` picks by `uname -m`: on x86_64 it uses `qemu-system-x86_64 -machine q35` and the amd64 image; on aarch64 it uses `qemu-system-aarch64 -machine virt,gic-version=host` with AAVMF firmware and the arm64 image. Everything else is shared.
