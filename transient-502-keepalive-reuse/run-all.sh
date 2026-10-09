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

docker build -t "$img" . > results/build.log 2>&1 || { tail -30 results/build.log; echo "RUN INVALID: docker build failed" > results/summary.md; exit 1; }
docker image inspect "$img" --format '{{.Id}}' >> results/00-environment.txt

invalid=()
bad() { invalid+=("$1: $2"); }
check() {
  local o=results/$1
  [ -s "$o/summary.txt" ] || { echo "no summary.txt"; return; }
  [ -s "$o/front-access.log" ] || { echo "nginx logged no request"; return; }
  if [ -f "$o/ab.log" ]; then
    grep -qE '^Complete requests: +[1-9]' "$o/ab.log" || { echo "ab did not complete"; return; }
  fi
  grep -qv '__close-idle' "$o/backend-access.log" 2>/dev/null || { echo "the backends received no request"; return; }
  [ -s "$o/loopback.txt" ] || { echo "no packet capture"; return; }
}
for sc in "${scs[@]}"; do
  o=results/$sc
  docker run --rm --cap-add=NET_ADMIN --cap-add=NET_RAW -v "$here":/lab "$img" bash /lab/scripts/scenario.sh "$sc" \
    || bad "$sc" "scenario.sh failed"
  if [ -f "$o/loopback.pcap" ]; then
    docker run --rm -v "$here":/lab "$img" bash -c \
      "tcpdump -r /lab/$o/loopback.pcap -n -tt 2>/dev/null > /lab/$o/loopback.txt" \
      || bad "$sc" "tcpdump -r failed"
  fi
  docker run --rm -v "$here":/lab "$img" chown -R "$(id -u):$(id -g)" /lab/results
  python3 scripts/analyze.py "$o" > "$o/analysis.txt" 2>&1 || bad "$sc" "analyze.py failed"
  reason=$(check "$sc")
  [ -z "$reason" ] || bad "$sc" "$reason"
done

{
  echo '```'; cat results/00-environment.txt; echo '```'
  for sc in "${scs[@]}"; do
    echo "## $sc"
    echo '```'; cat "results/$sc/summary.txt" 2>/dev/null || echo "no summary"; echo '```'
    echo '```'; cat "results/$sc/analysis.txt" 2>/dev/null; echo '```'
  done
  for x in ${invalid[@]+"${invalid[@]}"}; do echo "RUN INVALID: $x"; done
} > results/summary.md
cat results/summary.md
[ ${#invalid[@]} -eq 0 ]
