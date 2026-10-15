#!/bin/sh
set -eu
case "$1" in
  v3) b6=301 ;;
  *) b6=302 ;;
esac
sed -e "s/__TARGET__/$1/g" -e "s/__B6_STATUS__/$b6/g" /etc/nginx/docs.conf.template > /etc/nginx/conf.d/docs.conf
