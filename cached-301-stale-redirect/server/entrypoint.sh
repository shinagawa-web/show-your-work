#!/bin/sh
set -eu
t=$(( $(date +%s) - ${PAGE_AGE_SEC:-2592000} ))
find /usr/share/nginx/html -type f -exec touch -d "@$t" {} +
sed "s/__TARGET__/${TARGET:-v3}/g" /etc/nginx/docs.conf.template > /etc/nginx/conf.d/docs.conf
exec /docker-entrypoint.sh "$@"
