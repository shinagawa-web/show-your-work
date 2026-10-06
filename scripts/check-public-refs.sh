#!/usr/bin/env bash
set -euo pipefail

base=${1:-origin/main}
head=${2:-HEAD}
owner=shinagawa-web

scan_input() {
  git diff -U0 --no-color "$base...$head" | awk '
    /^\+\+\+ b\// { file = substr($0, 7); next }
    /^@@/ { split($3, a, ","); line = substr(a[1], 2); next }
    /^\+/ { print file ":" line "\t" substr($0, 2); line++ }
  '
  git log --format='%h%x09%B' "$base..$head" | awk -F'\t' '
    NF > 1 { sha = $1; $1 = ""; print "commit " sha "\t" substr($0, 2); next }
    { print "commit " sha "\t" $0 }
  '
}

extract_refs() {
  while IFS=$'\t' read -r where text; do
    { grep -oE 'github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+' <<<"$text" | sed 's|^github\.com/||' || true
      grep -oE "(^|[^A-Za-z0-9_./-])$owner/[A-Za-z0-9_.-]+#[0-9]+" <<<"$text" | sed -E "s|^.*($owner/)|\1|; s|#.*||" || true
    } | sed -E 's/\.git$//; s/[.]+$//' | while read -r ref; do printf '%s\t%s\n' "$where" "$ref"; done
  done
}

refs=$(scan_input | extract_refs | sort -u)
[ -z "$refs" ] && exit 0

unreachable=""
for ref in $(cut -f2 <<<"$refs" | sort -u); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://github.com/$ref")
  case $code in 2??|3??) ;; *) unreachable+="$ref"$'\t'"$code"$'\n' ;; esac
done
[ -z "$unreachable" ] && exit 0

failed=0
while IFS=$'\t' read -r where ref; do
  code=$(awk -F'\t' -v r="$ref" '$1 == r { print $2 }' <<<"$unreachable")
  [ -z "$code" ] && continue
  failed=1
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "::error::$where references a repository that is not publicly reachable (HTTP $code)"
  else
    echo "$where: $ref is not publicly reachable (HTTP $code)" >&2
  fi
done <<<"$refs"

exit $failed
