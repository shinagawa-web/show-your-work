#!/usr/bin/env bash
# One command: build images, install the runner, run the plan, summarize.
# Extra arguments go to runner/run.mjs (e.g. --sets grid --ages 100m).
set -euo pipefail
cd "$(dirname "$0")/.."
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
printf 'build_images_sec %.1f\ninstall_runner_sec %.1f\nrun_sec %.1f\nsummarize_sec %.1f\nrunner_exit %d\n' \
  "$(echo "$t1-$t0" | bc)" "$(echo "$t2-$t1" | bc)" "$(echo "$t3-$t2" | bc)" "$(echo "$t4-$t3" | bc)" "$rc" | tee "$out/durations.txt"
echo "$out"
exit $rc
