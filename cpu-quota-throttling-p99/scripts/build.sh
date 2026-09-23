#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
bin=$here/loadgen/bin/loadgen-$(uname -s | tr A-Z a-z)-$(uname -m)
if ! docker image inspect cqt-server >/dev/null 2>&1; then
  echo "build: cqt-server image" >&2
  docker build -q -t cqt-server "$here/server" >&2
fi
if [ ! -x "$bin" ] || [ "$here/loadgen/main.go" -nt "$bin" ]; then
  mkdir -p "$here/loadgen/bin"
  if command -v go >/dev/null; then
    echo "build: loadgen with $(go version)" >&2
    (cd "$here/loadgen" && CGO_ENABLED=0 go build -o "$bin" .) >&2
  else
    echo "build: loadgen with golang:1.25 container" >&2
    docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e CGO_ENABLED=0 -v "$here/loadgen:/src" -w /src golang:1.25 \
      go build -o "bin/$(basename "$bin")" . >&2
  fi
fi
echo "$bin"
