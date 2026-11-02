#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
build=$1
image=pws-$build:dev
if [ -f "$here/.images/$build.tar" ]; then
  docker load -q -i "$here/.images/$build.tar"
  exit 0
fi
case $build in
  gunicorn) args=(--build-arg PYTHON_IMAGE="$PYTHON_IMAGE" --build-arg GUNICORN_VERSION="$GUNICORN_VERSION" --build-arg GUNICORN_SHA256="$GUNICORN_SHA256") ;;
  spring) args=(--build-arg MAVEN_IMAGE="$MAVEN_IMAGE" --build-arg JRE_IMAGE="$JRE_IMAGE") ;;
  node) args=(--build-arg NODE_IMAGE="$NODE_IMAGE") ;;
  go) args=(--build-arg GO_IMAGE="$GO_IMAGE") ;;
  *) echo "unknown build $build" >&2; exit 2 ;;
esac
docker build -q "${args[@]}" -t "$image" "$here/servers/$build"
if [ "${SAVE_IMAGE:-}" = 1 ]; then
  mkdir -p "$here/.images"
  docker save -o "$here/.images/$build.tar" "$image"
fi
