#!/usr/bin/env bash
# Builds the site images used by runner/plan.mjs (IMAGES).
set -euo pipefail
cd "$(dirname "$0")/.."
build() {
  local name=$1; shift
  docker build -q -f server/Dockerfile -t "site-repro:$name" "$@" . >/dev/null
  echo "built site-repro:$name" >&2
}
build v1        --build-arg VERSION=v1
build v2        --build-arg VERSION=v2
build v2-keep   --build-arg VERSION=v2 --build-arg KEEP_OLD_ASSETS=1
build v1-reload --build-arg VERSION=v1 --build-arg RELOAD_ON_PRELOAD_ERROR=1
build v2-reload --build-arg VERSION=v2 --build-arg RELOAD_ON_PRELOAD_ERROR=1
for i in v1 v2 v2-keep v1-reload v2-reload; do
  echo "== site-repro:$i"
  docker run --rm --entrypoint cat "site-repro:$i" /site-files.txt
done
