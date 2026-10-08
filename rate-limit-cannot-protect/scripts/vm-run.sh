#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
vm=${VM:-rate-limit-cannot-protect}
COPYFILE_DISABLE=1 tar -C "$here" --no-xattrs --exclude results -cf - . |
  limactl shell "$vm" -- sh -c 'rm -rf ~/rl && mkdir -p ~/rl && tar -C ~/rl -xf - && cd ~/rl && ./scripts/run-all.sh "$@"' sh "$@"
rm -rf "$here/results"; mkdir -p "$here/results"
limactl shell "$vm" -- tar -C rl/results -cf - . | tar -C "$here/results" -xf -
