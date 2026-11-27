#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"
here=$(pwd)
start=$(date +%s.%N)
el() { awk -v a="$(date +%s.%N)" -v b="$start" 'BEGIN{printf "%.1f", a-b}'; }
export REDOCLY_SUPPRESS_UPDATE_NOTICE=true

rm -rf results
mkdir -p results/work

npm ci --no-audit --no-fund > results/npm-ci.log 2>&1 || { tail -30 results/npm-ci.log; exit 1; }
echo "[$(el)s] npm ci done"

workdir() {
  local d=$here/results/work/$1
  mkdir -p "$d"
  cp openapi.yaml "$d/"
  echo "$d"
}

lint() {
  local dir=$1 out=$2 rc
  shift 2
  (cd "$dir" && npx redocly lint openapi.yaml "$@") > "results/$out.txt" 2>&1
  rc=$?
  echo "exit code: $rc" >> "results/$out.txt"
  echo "[$(el)s] $out done (exit code $rc)"
}

section5() {
  local name=$1 patch=$2 d
  d=$here/results/work/$name
  cp -R "$s4" "$d"
  node scripts/patch.mjs "$d/openapi.yaml" "$patch" || exit 1
  lint "$d" "$name"
  lint "$d" "$name-github-actions" --format=github-actions
}

lint_json() {
  local dir=$1 out=$2
  (cd "$dir" && npx redocly lint openapi.yaml --format=json) > "results/$out.json" 2> "results/$out-json.stderr.txt"
}

s2=$(workdir section2)
lint "$s2" section2-lint
lint_json "$s2" section2-lint

s3=$(workdir section3)
cp redocly.yaml "$s3/"
lint "$s3" section3-lint
lint_json "$s3" section3-lint

s3n=$(workdir section3-without-extends)
awk '/^extends:/ { skip = 1; next } skip && /^  - / { next } { skip = 0; print }' redocly.yaml > "$s3n/redocly.yaml"
lint "$s3n" section3-lint-without-extends

s4=$(workdir section4)
cp redocly.yaml "$s4/"
(cd "$s4" && npx redocly lint --generate-ignore-file openapi.yaml) > results/section4-generate.txt 2>&1
echo "exit code: $?" >> results/section4-generate.txt
cp "$s4/.redocly.lint-ignore.yaml" results/section4-lint-ignore.yaml || echo "no .redocly.lint-ignore.yaml was generated" >&2
lint "$s4" section4-lint
lint_json "$s4" section4-lint

section5 section5-error-in-excluded-schema snake-case-property-in-order
section5 section5-warn-only string-quantity-example

s5r=$here/results/work/section5-regenerate
cp -R "$here/results/work/section5-error-in-excluded-schema" "$s5r"
cp "$s5r/.redocly.lint-ignore.yaml" results/section5-regenerate-ignore-before.yaml
(cd "$s5r" && npx redocly lint --generate-ignore-file openapi.yaml) > results/section5-regenerate-generate.txt 2>&1
echo "exit code: $?" >> results/section5-regenerate-generate.txt
cp "$s5r/.redocly.lint-ignore.yaml" results/section5-regenerate-ignore-after.yaml
(cd results && diff -u --label section5-regenerate-ignore-before.yaml --label section5-regenerate-ignore-after.yaml section5-regenerate-ignore-before.yaml section5-regenerate-ignore-after.yaml) > results/section5-regenerate-ignore.diff
lint "$s5r" section5-regenerate

(cd server && npm ci --no-audit --no-fund) > results/server-npm-ci.log 2>&1 || { tail -30 results/server-npm-ci.log; exit 1; }
echo "[$(el)s] server npm ci done"

order_id=3f6c2a1e-8b4d-4c3a-9e2f-1a2b3c4d5e6f
server_pid=
stop_server() {
  [ -n "$server_pid" ] || return 0
  kill "$server_pid" 2> /dev/null
  wait "$server_pid" 2> /dev/null
  server_pid=
}
trap stop_server EXIT

start_server() {
  local state=$1 skip=$2
  SKIP_ORDER_ACCESS_CHECK=$skip PORT=3000 node server/app.mjs > "results/section6-$state-server.log" 2>&1 &
  server_pid=$!
  for _ in $(seq 100); do
    curl -s -o /dev/null http://127.0.0.1:3000/ && return 0
    sleep 0.1
  done
  echo "RUN INVALID: the server did not answer on :3000" >&2
  exit 1
}

request() {
  local out=$1 who=$2 token=$3 body auth=()
  body=$(mktemp)
  [ -z "$token" ] || auth=(-H "Authorization: Bearer $token")
  {
    echo "request: GET /orders/$order_id as $who"
    curl -sS -o "$body" -w 'status: %{http_code}\n' "${auth[@]}" "http://127.0.0.1:3000/orders/$order_id"
    cat "$body"
    echo
  } > "results/section6-$out.txt"
  rm -f "$body"
}

start_server check 0
request check-a "user A (org X)" token-a
request check-b "user B (org Y)" token-b
request check-no-token "no token" ""
stop_server
echo "[$(el)s] section 6 with the order access check done"

start_server skip 1
request skip-a "user A (org X)" token-a
request skip-b "user B (org Y)" token-b
request skip-c "user C (org X)" token-c
request skip-no-token "no token" ""
stop_server
echo "[$(el)s] section 6 without the order access check done"

node scripts/summary.mjs results || exit 1

echo "[$(el)s] done" | tee results/total.txt
