#!/usr/bin/env bash
set -euo pipefail
SRC=${SRC:-/src}
RES=${RES:-/results}
WORK=${WORK:-/work}
. "$SRC/scripts/versions.env"
export GIT_AUTHOR_NAME=repro GIT_AUTHOR_EMAIL=repro@example.invalid GIT_COMMITTER_NAME=repro GIT_COMMITTER_EMAIL=repro@example.invalid
npm install -g --silent "pnpm@$PNPM_VERSION"
rm -rf "$WORK/base"
mkdir -p "$WORK"
cp -R "$SRC/workspace" "$WORK/base"
cd "$WORK/base"
pnpm install --frozen-lockfile
git init -q -b main
git add -A
git commit -qm base
{
  echo "node $(node --version)"
  echo "pnpm $(pnpm --version)"
  echo "turbo $(node_modules/.bin/turbo --version)"
  for m in packages/b/node_modules/vitest packages/b/node_modules/vite node_modules/ms; do
    echo "${m##*/} $(node -p 'JSON.parse(require("node:fs").readFileSync(process.argv[1] + "/package.json", "utf8")).version' "$m")"
  done
  echo "$(git --version)"
} > "$RES/versions.txt"
