#!/bin/sh
set -eu
sed "s/__TARGET__/$1/g" /etc/nginx/docs.conf.template > /etc/nginx/conf.d/docs.conf
nginx -t
nginx -s reload
