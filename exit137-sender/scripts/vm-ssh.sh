#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
vmdir=${VMDIR:-$here/.vm}
exec ssh -i "$vmdir/id" -p "${VM_SSH_PORT:-2222}" -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes ubuntu@127.0.0.1 "$@"
