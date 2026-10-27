#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
bin=$here/loadgen/bin/loadgen-$(uname -s | tr A-Z a-z)-$(uname -m)
if [ "${REBUILD:-0}" = 1 ] || ! docker image inspect mlr-app >/dev/null 2>&1; then
  echo "build: mlr-app image" >&2
  docker build -q -t mlr-app "$here/app" >&2
fi
if [ ! -x "$bin" ] || [ -n "$(find "$here/loadgen" -maxdepth 1 \( -name '*.go' -o -name go.mod \) -newer "$bin")" ]; then
  mkdir -p "$here/loadgen/bin"
  echo "build: loadgen with $(go version)" >&2
  (cd "$here/loadgen" && CGO_ENABLED=0 go build -o "$bin" .) >&2
fi
echo "$bin"
