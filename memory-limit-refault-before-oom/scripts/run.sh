#!/usr/bin/env bash
set -euo pipefail
if [ $# -lt 4 ]; then
  echo "usage: run.sh <outdir> <memory_mib> <cache_mib> <probe|base|n1|p3|p3b> [dur_s]   env: RPS ELEM_KB WORK_N EXTRA GROW_KBPS GROW_PER_REQUESTS FILL_DIR" >&2
  exit 2
fi
out=$1 mem=$2 cache=$3 mode=$4 dur=${5:-30}
rps=${RPS:-50} elem=${ELEM_KB:-1024} work_n=${WORK_N:-64} extra=${EXTRA:-} grow=${GROW_KBPS:-0} per_req=${GROW_PER_REQUESTS:-0}
fill_dir=${FILL_DIR:-/var/tmp/mlr-fill}
mem_b=$(python3 -c 'import sys;print(int(round(float(sys.argv[1])*1048576/4096))*4096)' "$mem")
here=$(cd "$(dirname "$0")/.." && pwd)
lg=$("$here/scripts/build.sh")
mkdir -p "$out" "$fill_dir"

docker rm -f mlr >/dev/null 2>&1 || true
trap 'docker rm -f mlr >/dev/null 2>&1 || true' EXIT
sync
echo 3 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null

id=$(docker run -d --name mlr --memory "$mem_b" --memory-swap "$mem_b" \
  -e CACHE_MB="$cache" -e ELEM_KB="$elem" -e WORK_N="$work_n" -e EXTRA="$extra" \
  -v "$fill_dir:/data:ro" mlr-app)
pid=$(docker inspect mlr --format '{{.State.Pid}}')
ip=$(docker inspect mlr --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
cg=/sys/fs/cgroup$(sed -n 's/^0:://p' "/proc/$pid/cgroup")
parent=$(dirname "$cg")
for _ in $(seq 300); do
  python3 -c 'import sys,urllib.request;sys.stdout.write(urllib.request.urlopen(sys.argv[1],timeout=2).read().decode())' "http://$ip:8000/info" > "$out/info.json" 2>/dev/null && break
  sleep 0.2
done
[ -s "$out/info.json" ] || { echo "app did not start" >&2; docker logs mlr >&2; exit 1; }
{
  echo "label=$(basename "$out")"
  echo "mode=$mode"
  echo "memory_mib=$mem"
  echo "memory_bytes=$mem_b"
  echo "cache_mib=$cache"
  echo "elem_kb=$elem"
  echo "work_n=$work_n"
  echo "extra=$extra"
  echo "rps=$rps"
  echo "dur_s=$dur"
  echo "grow_kbps=$grow"
  echo "grow_per_requests=$per_req"
  echo "container=$id"
  echo "image=$(docker inspect mlr --format '{{.Image}}')"
  echo "pid=$pid"
  echo "addr=$ip:8000"
  echo "cgroup=$cg"
  echo "parent_cgroup=$parent"
  echo "memory.max=$(cat "$cg/memory.max")"
  echo "memory.swap.max=$(cat "$cg/memory.swap.max")"
  echo "lru_gen_enabled=$(cat /sys/kernel/mm/lru_gen/enabled 2>/dev/null || echo none)"
  echo "swapon=$(swapon --show --noheadings --raw | tr '\n' ';')"
  echo "kernel=$(uname -r)"
} > "$out/meta.txt"

python3 "$here/scripts/collector.py" "$cg" "$parent" 1 1 > "$out/cgroup_start.jsonl"
"$lg" -addr "$ip:8000" -rps "$rps" -dur 15s -out "$out/warm.csv" 2>> "$out/loadgen.log"
python3 "$here/scripts/collector.py" "$cg" "$parent" 1 1 > "$out/cgroup_warm.jsonl"
if [ "$mode" = probe ]; then
  docker inspect mlr --format 'oom_killed={{.State.OOMKilled}} exit_code={{.State.ExitCode}} status={{.State.Status}}' > "$out/inspect.txt"
  exit 0
fi
if [ "$mode" = n1 ]; then
  size=$(stat -c %s "$fill_dir/fill.bin")
  echo "fill_file_bytes=$size" >> "$out/meta.txt"
  docker exec mlr dd if=/data/fill.bin of=/dev/null bs=1M 2>> "$out/fill.log"
fi

python3 "$here/scripts/collector.py" "$cg" "$parent" 1 $((dur + 3)) > "$out/cgroup.jsonl" &
cpid=$!
sleep 2
case "$mode" in
  p3) q="kbps=$grow" ;;
  p3b) q="per_requests=$per_req" ;;
  *) q= ;;
esac
if [ -n "$q" ]; then
  python3 -c 'import sys,urllib.request;print(urllib.request.urlopen(urllib.request.Request(sys.argv[1],method="POST"),timeout=5).read().decode())' "http://$ip:8000/grow?$q" > "$out/grow.json"
fi
"$lg" -addr "$ip:8000" -rps "$rps" -dur "${dur}s" -out "$out/requests.csv" 2>> "$out/loadgen.log" &
lpid=$!
if [ -n "$q" ]; then
  while kill -0 "$lpid" 2>/dev/null; do
    if [ "$(docker inspect mlr --format '{{.State.Running}}' 2>/dev/null)" != true ]; then
      echo "container_stopped_unix=$(date +%s.%N)" >> "$out/meta.txt"
      sleep 1
      kill -TERM "$lpid" 2>/dev/null || true
      break
    fi
    sleep 0.2
  done
fi
wait "$lpid"
if [ -n "$q" ]; then kill "$cpid" 2>/dev/null || true; fi
wait "$cpid" || true
python3 "$here/scripts/collector.py" "$cg" "$parent" 1 1 > "$out/cgroup_end.jsonl"
docker inspect mlr --format 'oom_killed={{.State.OOMKilled}} exit_code={{.State.ExitCode}} status={{.State.Status}}' > "$out/inspect.txt"
docker logs mlr 2>&1 | grep '^GROW ' > "$out/grow.log" || true
docker logs mlr 2>&1 | grep -v -e '"GET / HTTP/1.1" 200' -e '^GROW ' | tail -200 > "$out/app.log" || true
