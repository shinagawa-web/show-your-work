#!/bin/sh
set -eu
t=$(( $(date +%s) - ${PAGE_AGE_SEC:-2592000} ))
find /usr/share/nginx/html -type f -exec touch -d "@$t" {} +
render-conf "${TARGET:-v3}"
exec /docker-entrypoint.sh "$@"
