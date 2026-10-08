#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ts() { date +%s.%N; }
out="results/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$out"
t0=$(ts)
scripts/build-images.sh > "$out/images.txt"
t1=$(ts)
(cd runner && npm ci --no-audit --no-fund >/dev/null && npx playwright install --with-deps chromium-headless-shell >/dev/null)
t2=$(ts)
set +e
(cd runner && node run.mjs --out "../$out" "$@") 2> "$out/runner.log"
rc=$?
set -e
t3=$(ts)
python3 scripts/summarize.py "$out/results.json" > "$out/summary.md" || true
t4=$(ts)
sec() { awk -v a="$2" -v b="$1" 'BEGIN{printf "%.1f", a-b}'; }
printf 'build_images_sec %s\ninstall_runner_sec %s\nrun_sec %s\nsummarize_sec %s\nrunner_exit %d\n' \
  "$(sec "$t0" "$t1")" "$(sec "$t1" "$t2")" "$(sec "$t2" "$t3")" "$(sec "$t3" "$t4")" "$rc" | tee "$out/durations.txt"
echo "$out"
exit $rc
