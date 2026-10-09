#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
if [ $# -gt 0 ]; then scs=("$@"); else
  scs=(baseline-concurrent baseline-concurrent-noturnover retry-get retry-post nonidem-post ka-timeout drain)
fi
img=keepalive-lab
rm -rf results; mkdir -p results
{
  uname -srm; nproc; free -h | head -2
  docker version --format 'docker {{.Server.Version}}'
} > results/00-environment.txt 2>&1

docker build -t "$img" . > results/build.log 2>&1 || { tail -30 results/build.log; exit 1; }
docker image inspect "$img" --format '{{.Id}}' >> results/00-environment.txt

status=0
for sc in "${scs[@]}"; do
  o=results/$sc
  docker run --rm --cap-add=NET_ADMIN --cap-add=NET_RAW -v "$here":/lab "$img" bash /lab/scripts/scenario.sh "$sc" \
    || { echo "$sc: scenario.sh failed"; status=1; }
  if [ -f "$o/loopback.pcap" ]; then
    docker run --rm -v "$here":/lab "$img" bash -c \
      "tcpdump -r /lab/$o/loopback.pcap -n -tt 2>/dev/null > /lab/$o/loopback.txt" \
      || { echo "$sc: tcpdump -r failed"; status=1; }
  fi
  docker run --rm -v "$here":/lab "$img" chown -R "$(id -u):$(id -g)" /lab/results
  python3 scripts/analyze.py "$o" > "$o/analysis.txt" 2>&1 || { echo "$sc: analyze.py failed"; status=1; }
done

{
  echo '```'; cat results/00-environment.txt; echo '```'
  for sc in "${scs[@]}"; do
    echo "## $sc"
    echo '```'; cat "results/$sc/summary.txt" 2>/dev/null || echo "no summary"; echo '```'
    echo '```'; cat "results/$sc/analysis.txt" 2>/dev/null; echo '```'
  done
} > results/summary.md
cat results/summary.md
exit "$status"
