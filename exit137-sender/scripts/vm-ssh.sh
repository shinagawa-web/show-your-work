#!/usr/bin/env bash
# Run a command in the test VM.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
vmdir=${VMDIR:-$here/.vm}
exec ssh -i "$vmdir/id" -p "${VM_SSH_PORT:-2222}" -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes ubuntu@127.0.0.1 "$@"
