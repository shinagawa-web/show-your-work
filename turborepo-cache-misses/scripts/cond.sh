#!/usr/bin/env bash
set -uo pipefail
SRC=${SRC:-/src}
RES=${RES:-/results}
WORK=${WORK:-/work}
. "$SRC/scripts/changes.sh"
export TURBO_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 TURBO_NO_UPDATE_NOTIFIER=1 NO_COLOR=1
export GIT_AUTHOR_NAME=repro GIT_AUTHOR_EMAIL=repro@example.invalid GIT_COMMITTER_NAME=repro GIT_COMMITTER_EMAIL=repro@example.invalid

field() { awk -F'\t' -v n="$1" -v k="$2" 'NR == 1 { for (i = 1; i <= NF; i++) c[$i] = i } NR > 1 && $1 == n { print $c[k] }' "$SRC/conditions.tsv"; }
strip() { sed 's/\x1b\[[0-9;?]*[A-Za-z]//g'; }

run() {
  local name=$1
  shift
  local log=$out/$name.log
  echo "\$ $*" > "$log"
  rm -rf .turbo/runs
  "$@" 2>&1 | strip >> "$log"
  local rc=${PIPESTATUS[0]}
  echo "$rc" > "$out/$name.rc"
  local summary
  summary=$(ls .turbo/runs/*.json 2>/dev/null | head -1)
  [ -z "$summary" ] || cp "$summary" "$out/$name.summary.json"
  return 0
}

correct() {
  (cd packages/b && run correct-b node_modules/.bin/vitest run --reporter=default --reporter=json --outputFile.json="$out/correct-b.json")
}

base_path=$PATH
for name in "$@"; do
  out=$RES/$name
  rm -rf "$out"
  mkdir -p "$out"
  t0=$(date +%s.%N)
  after=$(field "$name" after)
  setup=$(field "$name" setup)
  change=$(field "$name" change)
  mode=$(field "$name" mode)
  app_mode=$(field "$name" app_mode)
  dir=$WORK/$name
  rm -rf "$dir"
  if [ "$after" = - ]; then
    cp -a "$WORK/base" "$dir"
  else
    cp -a "$WORK/$after" "$dir"
  fi
  cd "$dir"
  export PATH=$dir/node_modules/.bin:$base_path
  pnpm install --frozen-lockfile > "$out/install.log" 2>&1
  if [ "$after" = - ]; then
    if [ "$setup" != - ]; then
      for s in ${setup//,/ }; do "setup_$s"; done
      git add -A && git commit -qm "setup: $setup"
    fi
    cp turbo.json "$out/turbo.json"
    APP_MODE=old run cache-build turbo run test --ui=stream --summarize
    [ "$(cat "$out/cache-build.rc")" = 0 ] || echo "cache build on main did not pass" >> "$out/invalid.txt"
    git rev-parse HEAD > "$out/cache-build.sha"
  else
    cp "$RES/$after/cache-build.sha" "$out/cache-build.sha"
    cp turbo.json "$out/turbo.json"
  fi
  export APP_MODE=$app_mode
  case $mode in
    main | main-cleared)
      git checkout -q -B mainsim pr
      git rev-parse HEAD > "$out/head.sha"
      [ "$mode" = main ] || rm -rf .turbo/cache
      run dry turbo run test --dry=json
      run cache turbo run test --ui=stream --summarize
      if [ "$mode" = main ]; then
        run cache-verbose turbo run test --ui=stream --summarize -v
        GITHUB_ACTIONS=true run cache-gha turbo run test --ui=stream --summarize
      fi
      ;;
    pr | pr-force)
      if [ "$after" = - ]; then
        git checkout -q -b pr
      else
        git checkout -q -b "$name"
      fi
      "change_$change" > "$out/change.log" 2>&1
      git add -A && git commit -q --allow-empty -m "change: $change"
      git show --stat --format='%H %s' HEAD > "$out/change.stat"
      git rev-parse HEAD > "$out/head.sha"
      pnpm install --frozen-lockfile >> "$out/install.log" 2>&1
      run dry turbo run test --dry=json
      run cache turbo run test --ui=stream --summarize
      [ "$mode" = pr ] || run force turbo run test --ui=stream --summarize --force
      ;;
  esac
  correct
  unset APP_MODE
  awk -v a="$(date +%s.%N)" -v b="$t0" 'BEGIN { printf "%.1f\n", a - b }' > "$out/seconds"
  echo "$name done in $(cat "$out/seconds")s"
done
