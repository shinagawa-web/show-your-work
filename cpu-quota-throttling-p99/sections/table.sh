#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
case "${1:-}" in
  s5) CONDS=("${S5_CONDS[@]}") ;;
  s9) CONDS=("${S9_CONDS[@]}") ;;
  *) echo "usage: table.sh <s5|s9>" >&2; exit 2 ;;
esac
table_for "$1"
