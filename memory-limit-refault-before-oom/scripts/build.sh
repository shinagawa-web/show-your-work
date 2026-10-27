#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
bin=$here/loadgen/bin/loadgen-$(uname -s | tr A-Z a-z)-$(uname -m)
docker build -q -t mlr-app "$here/app" >/dev/null
if [ ! -x "$bin" ] || [ -n "$(find "$here/loadgen" -maxdepth 1 \( -name '*.go' -o -name go.mod \) -newer "$bin")" ]; then
  mkdir -p "$here/loadgen/bin"
  echo "build: loadgen with $(go version)" >&2
  (cd "$here/loadgen" && CGO_ENABLED=0 go build -o "$bin" .) >&2
fi
echo "$bin"
