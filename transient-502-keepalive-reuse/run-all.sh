#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
if [ $# -gt 0 ]; then conds=("$@"); else
  conds=(baseline idle-close idle-close-nginx-shorter)
fi
img=transient-502-keepalive-reuse
rm -rf results; mkdir -p results
{
  uname -srm; nproc; free -h | head -2
  docker version --format 'docker {{.Server.Version}}'
} > results/environment.txt 2>&1
docker build -t "$img" . > results/build.log 2>&1 || { tail -30 results/build.log; echo "RUN INVALID: docker build failed" > results/summary.md; exit 1; }

rc=0
for c in "${conds[@]}"; do
  start=$(date +%s)
  docker run --rm --cap-add=NET_RAW -v "$here":/lab "$img" bash scripts/run.sh "$c" || { echo "RUN INVALID: run.sh failed" >> "results/$c/analysis.txt"; rc=1; }
  docker run --rm -v "$here":/lab "$img" chown -R "$(id -u):$(id -g)" /lab/results
  echo "duration s: $(( $(date +%s) - start ))" >> "results/$c/settings.txt"
  python3 scripts/analyze.py "results/$c" >> "results/$c/analysis.txt" 2>&1 || rc=1
done

{
  echo '```'; cat results/environment.txt; echo '```'
  for c in "${conds[@]}"; do
    echo "# $c"
    echo '```'; cat "results/$c/analysis.txt" 2>/dev/null || echo "no analysis"; echo '```'
  done
} > results/summary.md
cat results/summary.md
exit $rc
