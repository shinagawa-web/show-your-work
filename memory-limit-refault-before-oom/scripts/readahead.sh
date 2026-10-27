#!/usr/bin/env bash
set -euo pipefail
root=$(docker info --format '{{.DockerRootDir}}')
dev=$(findmnt -no MAJ:MIN --target "$root" | head -1 | tr -d ' ')
p=$(readlink -f "/sys/dev/block/$dev")
[ ! -f "$p/partition" ] || p=$(dirname "$p")
echo "$p/queue/read_ahead_kb"
