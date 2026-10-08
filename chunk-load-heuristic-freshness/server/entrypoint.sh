#!/bin/sh
# Runs at container start.
#
# AGE_SEC              mtime of every served file = start time - AGE_SEC
#                      (Last-Modified = time since the previous deploy)
# AGE_HEADER           if set, index.html is served with "Age: <value>"
#                      (simulates the time between fetch and revisit)
# SPA_FALLBACK=1       unknown paths are answered with index.html (200)
# INDEX_CACHE_CONTROL  if set, index.html is served with this Cache-Control
# LOG_IF_HEADERS=1     access log also records If-None-Match / If-Modified-Since
#
# With none of AGE_HEADER / SPA_FALLBACK / INDEX_CACHE_CONTROL set,
# the stock /etc/nginx/conf.d/default.conf of the image is left untouched.
set -eu

now=$(date +%s)
t=$(( now - ${AGE_SEC:-0} ))
find /usr/share/nginx/html -type f -exec touch -d "@$t" {} +
echo "entrypoint: start=$now AGE_SEC=${AGE_SEC:-0} mtime=$t" >&2

if [ -n "${AGE_HEADER:-}" ] || [ "${SPA_FALLBACK:-0}" = 1 ] || [ -n "${INDEX_CACHE_CONTROL:-}" ] || [ "${LOG_IF_HEADERS:-0}" = 1 ]; then
  log_format=""
  access_log=""
  if [ "${LOG_IF_HEADERS:-0}" = 1 ]; then
    # log_format "main" of the image plus the two conditional request headers
    log_format='log_format cond '"'"'$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent" "$http_x_forwarded_for" inm="$http_if_none_match" ims="$http_if_modified_since"'"'"';'
    access_log='access_log /var/log/nginx/access.log cond;'
  fi
  fallback=""
  [ "${SPA_FALLBACK:-0}" = 1 ] && fallback='try_files $uri /index.html;'
  index_headers=""
  [ -n "${AGE_HEADER:-}" ] && index_headers="$index_headers add_header Age \"$AGE_HEADER\";"
  [ -n "${INDEX_CACHE_CONTROL:-}" ] && index_headers="$index_headers add_header Cache-Control \"$INDEX_CACHE_CONTROL\";"
  cat > /etc/nginx/conf.d/default.conf <<CONF
$log_format
server {
    $access_log
    listen       80;
    listen  [::]:80;
    server_name  localhost;

    location / {
        root   /usr/share/nginx/html;
        index  index.html index.htm;
        $fallback
    }

    location = /index.html {
        root   /usr/share/nginx/html;
        $index_headers
    }

    error_page   500 502 503 504  /50x.html;
    location = /50x.html {
        root   /usr/share/nginx/html;
    }
}
CONF
  echo "entrypoint: generated default.conf" >&2
  cat /etc/nginx/conf.d/default.conf >&2
fi

exec /docker-entrypoint.sh "$@"
