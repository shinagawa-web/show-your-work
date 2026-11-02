#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
export PATH=$here/.bin:$PATH
work=$1
other=$2
shift 2
export DOCKER_CONFIG=$work/crane-config
for img in "$@"; do
  case $img in
    app) crane copy "$BUSYBOX_SOURCE" localhost:5001/app:1.0 >&2 ;;
    platform-only) crane index filter --platform "linux/$other" "$BUSYBOX_SOURCE" -t localhost:5001/platform-only:1.0 >&2 ;;
    single-arch) crane copy --platform "linux/$other" "$BUSYBOX_SOURCE" localhost:5001/single-arch:1.0 >&2 ;;
  esac
done
for img in "$@"; do
  ref=localhost:5001/$img:1.0
  echo "## $ref"
  m=$(crane manifest "$ref")
  echo "$m" | jq -c '{mediaType, manifests: [.manifests[]? | {digest, platform}], config: .config.digest}'
  if echo "$m" | jq -e '.config' >/dev/null; then crane config "$ref" | jq -c '{architecture, os}'; fi
done
