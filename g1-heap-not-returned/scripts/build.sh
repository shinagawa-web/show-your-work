#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
app=${RESULTS:-$here/results}/app; mkdir -p "$app"
chmod 777 "$app"
docker run --rm -v "$here/app:/src:ro" -v "$app:/app" "$JDK8_IMAGE" javac -d /app /src/Alloc.java
