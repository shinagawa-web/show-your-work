#!/usr/bin/env bash
set -euo pipefail
if [ $# -lt 4 ]; then
  echo "usage: run.sh <outdir> <memory_mib> <cache_mib> <probe|base|n1|p3> [dur_s]   env: RPS ELEM_KB WORK_N EXTRA GROW_KBPS FILL_DIR" >&2
  exit 2
fi
out=$1 mem=$2 cache=$3 mode=$4 dur=${5:-30}
rps=${RPS:-50} elem=${ELEM_KB:-1024} work_n=${WORK_N:-64} extra=${EXTRA:-} grow=${GROW_KBPS:-0}
fill_dir=${FILL_DIR:-/var/tmp/mlr-fill}
touch=${TOUCH:-1}
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
upper=$(docker inspect mlr --format '{{.GraphDriver.Data.UpperDir}}' 2>/dev/null || true)
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
  echo "container=$id"
  echo "image=$(docker inspect mlr --format '{{.Image}}')"
  echo "pid=$pid"
  echo "addr=$ip:8000"
  echo "cgroup=$cg"
  echo "parent_cgroup=$parent"
  echo "memory.max=$(cat "$cg/memory.max")"
  echo "memory.swap.max=$(cat "$cg/memory.swap.max")"
  echo "memory.high=$(cat "$cg/memory.high")"
  echo "lru_gen_enabled=$(cat /sys/kernel/mm/lru_gen/enabled 2>/dev/null || echo none)"
  echo "swapon=$(swapon --show --noheadings --raw | tr '\n' ';')"
  echo "storage_driver=$(docker info --format '{{.Driver}}')"
  echo "docker_root_device=$(findmnt -no SOURCE,FSTYPE --target "$(docker info --format '{{.DockerRootDir}}')" | head -1)"
  echo "upperdir=$upper"
  echo "fill_dir_device=$(findmnt -no SOURCE,FSTYPE --target "$fill_dir" | head -1)"
  echo "kernel=$(uname -r)"
  ra=$("$here/scripts/readahead.sh")
  echo "read_ahead_kb_path=$ra"
  echo "read_ahead_kb=$(cat "$ra")"
} > "$out/meta.txt"

python3 "$here/scripts/collector.py" "$cg" "$parent" 1 1 > "$out/cgroup_start.jsonl"
"$lg" -addr "$ip:8000" -rps "$rps" -dur 5s -out "$out/warm.csv" 2>> "$out/loadgen.log"
python3 "$here/scripts/collector.py" "$cg" "$parent" 1 1 > "$out/cgroup_warm.jsonl"
if [ "$mode" = probe ]; then
  docker inspect mlr --format 'oom_killed={{.State.OOMKilled}} exit_code={{.State.ExitCode}} status={{.State.Status}}' > "$out/inspect.txt"
  exit 0
fi
if [ "$touch" = 1 ]; then
  echo 1 | sudo -n tee "/proc/$pid/clear_refs" >/dev/null
  python3 "$here/scripts/collector.py" "$cg" "$parent" 1 11 > "$out/cgroup_touch.jsonl" &
  cpid=$!
  "$lg" -addr "$ip:8000" -rps "$rps" -dur 10s -out "$out/touch.csv" 2>> "$out/loadgen.log"
  sudo -n python3 "$here/scripts/smaps.py" "$pid" > "$out/smaps.json"
  wait "$cpid"
fi

if [ "$mode" = n1 ]; then
  size=$(stat -c %s "$fill_dir/fill.bin")
  echo "fill_file_bytes=$size" >> "$out/meta.txt"
  s=$(date +%s.%N)
  docker exec mlr dd if=/data/fill.bin of=/dev/null bs=1M 2>> "$out/fill.log"
  echo "fill_read_s=$(python3 -c "import sys,time;print(round(time.time()-float(sys.argv[1]),2))" "$s")" >> "$out/meta.txt"
fi

python3 "$here/scripts/collector.py" "$cg" "$parent" 1 $((dur + 3)) > "$out/cgroup.jsonl" &
cpid=$!
sleep 2
if [ "$mode" = p3 ]; then
  python3 -c 'import sys,urllib.request;print(urllib.request.urlopen(urllib.request.Request(sys.argv[1],method="POST"),timeout=5).read().decode())' "http://$ip:8000/grow?kbps=$grow" > "$out/grow.json"
fi
"$lg" -addr "$ip:8000" -rps "$rps" -dur "${dur}s" -out "$out/requests.csv" 2>> "$out/loadgen.log" &
lpid=$!
if [ "$mode" = p3 ]; then
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
if [ "$mode" = p3 ]; then kill "$cpid" 2>/dev/null || true; fi
wait "$cpid" || true
python3 "$here/scripts/collector.py" "$cg" "$parent" 1 1 > "$out/cgroup_end.jsonl"
docker inspect mlr --format 'oom_killed={{.State.OOMKilled}} exit_code={{.State.ExitCode}} status={{.State.Status}}' > "$out/inspect.txt"
docker logs mlr 2>&1 | grep -v '"GET / HTTP/1.1" 200' | tail -200 > "$out/app.log" || true
