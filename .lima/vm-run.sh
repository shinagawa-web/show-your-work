#!/usr/bin/env bash
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: .lima/vm-run.sh <slug> [run-all.sh args ...]" >&2; exit 2; }
slug=${1%/}
shift
root=$(cd "$(dirname "$0")/.." && pwd)
[ -x "$root/$slug/run-all.sh" ] || { echo "$slug/run-all.sh not found" >&2; exit 2; }
vm=${VM:-$slug}
set +e
(cd "$root" && git ls-files -z -co --exclude-standard -- "$slug" | COPYFILE_DISABLE=1 tar --no-xattrs --null -T - -cf -) |
  limactl shell "$vm" -- sh -c 's=$1; shift; rm -rf ~/"$s" && tar -C ~ -xf - && cd ~/"$s" && ./run-all.sh "$@"' sh "$slug" "$@"
rc=${PIPESTATUS[1]}
set -e
rm -rf "$root/$slug/results"
mkdir -p "$root/$slug/results"
limactl shell "$vm" -- sh -c 'tar -C ~/"$1"/results -cf - .' sh "$slug" | tar -C "$root/$slug/results" -xf -
exit "$rc"
