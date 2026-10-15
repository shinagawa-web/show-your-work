#!/bin/sh
set -eu
render-conf "$1"
nginx -t
nginx -s reload
