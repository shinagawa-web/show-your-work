#!/usr/bin/env bash
set -euo pipefail
if [ $# -lt 4 ]; then
  echo "usage: run.sh <outdir> <cpus> <W> <rps> [poisson|uniform] [dur_s]   env: EXEC SAME_HOST SEED BURST CPU_MS" >&2
  exit 2
fi
out=$1 cpus=$2 w=$3 rps=$4 mode=${5:-poisson} dur=${6:-60}
seed=${SEED:-1} burst=${BURST:-1} cpu_ms=${CPU_MS:-20}
exec_=${EXEC-limactl shell cpu-quota-throttling-p99 --}
same_host=${SAME_HOST:-0}
here=$(cd "$(dirname "$0")/.." && pwd)
lg=$here/loadgen/loadgen
tail=$((dur + 8))
x() { $exec_ "$@"; }

mkdir -p "$out"
[ -x "$lg" ] || (cd "$here/loadgen" && go build -o loadgen .)
x docker image inspect cqt-server >/dev/null 2>&1 || (cd "$here/server" && COPYFILE_DISABLE=1 tar --no-xattrs -cf - Dockerfile main.go 2>/dev/null || tar -cf - Dockerfile main.go) | x docker build -q -t cqt-server - >/dev/null

x docker rm -f cqt >/dev/null 2>&1 || true
x pkill -f cqt-clocksrv >/dev/null 2>&1 || true
id=$(x docker run -d --name cqt --cpus "$cpus" -e W="$w" -e CPU_MS="$cpu_ms" -p 18080:8080 cqt-server)
pid=$(x docker inspect cqt --format '{{.State.Pid}}')
cg=/sys/fs/cgroup$(x cat "/proc/$pid/cgroup" | sed -n 's/^0:://p')
cgid=$(x stat -c %i "$cg")
until x sudo nsenter -t "$pid" -n ss -lntH 'sport = :8080' | grep -q LISTEN; do sleep 0.2; done
{
  echo "label=$(basename "$out") cpus=$cpus W=$w rps=$rps mode=$mode dur=$dur seed=$seed burst=$burst cpu_ms=$cpu_ms"
  echo "container=$id"
  echo "image=$(x docker inspect cqt --format '{{.Image}}')"
  echo "cgroup=$cg"
  echo "cgroup_id=$cgid"
  echo "pid=$pid"
  echo "cpu.max=$(x cat "$cg/cpu.max")"
  echo "kernel=$(x uname -r)"
  echo "nproc=$(x nproc)"
  echo "host=$(x hostname)"
  echo "cpu_model=$(x lscpu | sed -n 's/^Model name: *//p' | head -1)"
  echo "exec=${exec_:-local}"
  echo "same_host=$same_host"
  echo "loadgen_host=$(uname -srm)"
} > "$out/meta.txt"

sync_addr=127.0.0.1:18081
if [ "$same_host" = 1 ]; then
  sync_addr=
else
  x timeout $((tail + 30)) python3 -c "$(cat "$here/scripts/clocksrv.py")" 18081 cqt-clocksrv &
fi
now() { python3 -c 'import time; print(f"{time.time():.3f}")'; }
bt_start=$(now)
x sudo timeout "$tail" bpftrace -e "$(cat "$here/scripts/throttle.bt")" "$cgid" > "$out/throttle_raw.txt" 2>&1 &
p0=$!
x python3 -c "$(cat "$here/scripts/collector.py")" "$cg" 1 "$tail" > "$out/cpustat_1s.csv" &
p1=$!
x python3 -c "$(cat "$here/scripts/collector.py")" "$cg" 0.01 "$tail" > "$out/cpustat_10ms.csv" &
p2=$!
x sudo python3 -c "$(cat "$here/scripts/recvq.py")" "$pid" 0.01 "$tail" 8080 > "$out/recvq_10ms.csv" &
p3=$!
for _ in $(seq 150); do grep -q Attaching "$out/throttle_raw.txt" 2>/dev/null && break; sleep 0.2; done
grep -q Attaching "$out/throttle_raw.txt" || { echo "bpftrace did not attach:" >&2; cat "$out/throttle_raw.txt" >&2; exit 1; }
echo "bpftrace_attach_s=$(python3 -c "print(f'{$(now) - $bt_start:.3f}')")" >> "$out/meta.txt"
sleep 1
"$lg" -rps "$rps" -dur "${dur}s" -mode "$mode" -seed "$seed" -burst "$burst" -sync "$sync_addr" -out "$out/requests.csv" 2>> "$out/meta.txt"
wait $p0 $p1 $p2 $p3
x pkill -f cqt-clocksrv >/dev/null 2>&1 || true
x docker rm -f cqt >/dev/null
