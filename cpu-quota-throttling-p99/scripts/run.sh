#!/usr/bin/env bash
set -euo pipefail
if [ $# -lt 4 ]; then
  echo "usage: run.sh <outdir> <cpus> <W> <rps> [poisson|uniform] [dur_s]   env: CQT_BPFTRACE SEED BURST CPU_MS" >&2
  exit 2
fi
out=$1 cpus=$2 w=$3 rps=$4 mode=${5:-poisson} dur=${6:-60}
seed=${SEED:-1} burst=${BURST:-1} cpu_ms=${CPU_MS:-20}
here=$(cd "$(dirname "$0")/.." && pwd)
bpf=${CQT_BPFTRACE:-0}
tail=$((dur + 8))

mkdir -p "$out"
lg=$("$here/scripts/build.sh")

docker rm -f cqt >/dev/null 2>&1 || true
id=$(docker run -d --name cqt --cpus "$cpus" -e W="$w" -e CPU_MS="$cpu_ms" -p 127.0.0.1:18080:8080 cqt-server)
trap 'docker rm -f cqt >/dev/null 2>&1 || true' EXIT
pid=$(docker inspect cqt --format '{{.State.Pid}}')
cg=/sys/fs/cgroup$(sed -n 's/^0:://p' "/proc/$pid/cgroup")
cgid=$(stat -c %i "$cg")
until sudo -n nsenter -t "$pid" -n ss -lntH 'sport = :8080' | grep -q LISTEN; do sleep 0.2; done
{
  echo "label=$(basename "$out") cpus=$cpus W=$w rps=$rps mode=$mode dur=$dur seed=$seed burst=$burst cpu_ms=$cpu_ms"
  echo "container=$id"
  echo "image=$(docker inspect cqt --format '{{.Image}}')"
  echo "cgroup=$cg"
  echo "cgroup_id=$cgid"
  echo "pid=$pid"
  echo "cpu.max=$(cat "$cg/cpu.max")"
  echo "kernel=$(uname -r)"
  echo "nproc=$(nproc)"
  echo "host=$(hostname)"
  echo "cpu_model=$(lscpu | sed -n 's/^Model name: *//p' | head -1)"
  echo "bpftrace=$bpf"
} > "$out/meta.txt"

pids=()
if [ "$bpf" = 1 ]; then
  sudo -n timeout "$tail" "$(command -v bpftrace)" "$here/scripts/throttle.bt" "$cgid" > "$out/throttle_raw.txt" 2>&1 &
  pids+=($!)
  for _ in $(seq 150); do grep -q Attaching "$out/throttle_raw.txt" 2>/dev/null && break; sleep 0.2; done
  grep -q Attaching "$out/throttle_raw.txt" || { echo "bpftrace did not attach:" >&2; cat "$out/throttle_raw.txt" >&2; exit 1; }
fi
python3 "$here/scripts/collector.py" "$cg" 1 "$tail" > "$out/cpustat_1s.csv" &
pids+=($!)
python3 "$here/scripts/collector.py" "$cg" 0.01 "$tail" > "$out/cpustat_10ms.csv" &
pids+=($!)
sudo -n python3 "$here/scripts/recvq.py" "$pid" 0.01 "$tail" 8080 > "$out/recvq_10ms.csv" &
pids+=($!)
sleep 1
"$lg" -rps "$rps" -dur "${dur}s" -mode "$mode" -seed "$seed" -burst "$burst" -out "$out/requests.csv" 2>> "$out/meta.txt"
wait "${pids[@]}"
