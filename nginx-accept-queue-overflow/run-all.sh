#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
reps=${REPS:-3}
tools=nginx-aq-tools
if [ $# -gt 0 ]; then targets=("$@"); else targets=($(awk -F'\t' 'NR > 1 {print $1}' conditions.tsv) somaxconn); fi

t0=$(date +%s)
rm -rf results; mkdir -p results
docker build -t "$tools" . > results/build.log 2>&1 || { tail -30 results/build.log; exit 1; }
docker pull -q nginx:1.30.6 >/dev/null
docker pull -q node:22.22.3-bookworm-slim >/dev/null
{
  echo "\$ uname -srm"; uname -srm
  echo "\$ nproc"; nproc
  echo "\$ docker version --format '{{.Server.Version}}'"; docker version --format '{{.Server.Version}}'
  echo "\$ sysctl (host network namespace)"
  sysctl net.core.somaxconn net.ipv4.tcp_syn_linear_timeouts net.ipv4.tcp_syn_retries net.ipv4.tcp_abort_on_overflow
  echo "\$ conditions.tsv"; cat conditions.tsv
} > results/00-environment.txt 2>&1
t1=$(date +%s)
echo "setup: $((t1 - t0)) s" > results/timing.txt

for t in "${targets[@]}"; do
  ts=$(date +%s)
  if [ "$t" = somaxconn ]; then
    bash scripts/somaxconn.sh
  else
    for r in $(seq "$reps"); do
      echo "== $t rep-$r"
      bash scripts/scenario.sh "$t" "$r"
      docker run --rm -v "$here":/lab "$tools" python3 /lab/scripts/analyze.py "/lab/results/$t/rep-$r" > /dev/null
    done
  fi
  echo "$t: $(( $(date +%s) - ts )) s" >> results/timing.txt
done

docker run --rm -v "$here":/lab "$tools" sh -c "python3 /lab/scripts/summarize.py /lab/results ${targets[*]} > /lab/results/summary.md; chown -R $(id -u):$(id -g) /lab/results"
echo "total: $(( $(date +%s) - t0 )) s" >> results/timing.txt
cat results/timing.txt
! grep -q 'RUN INVALID' results/summary.md
