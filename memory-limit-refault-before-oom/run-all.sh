#!/usr/bin/env bash
set -euo pipefail
usage() {
  cat >&2 <<'EOF2'
usage: ./run-all.sh [target ...]

targets (default: s3:n1 s3:p3 s3:p3b s3:f1):
  s3:n1 s3:f1               a baseline, then the condition
  s3:p3                     under load, a timer task adds to the in-process cache at a fixed rate, from the baseline until OOM kill or 180 s
  s3:p3b                    under load, every Nth request adds to the in-process cache, from the baseline until OOM kill or 180 s

env:
  RESULTS=results           output directory

The run ends by writing $RESULTS/summary.md from everything found in $RESULTS.
EOF2
}
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
case "${1:-}" in -h|--help) usage; exit 0 ;; esac
targets=("$@")
[ ${#targets[@]} -gt 0 ] || targets=(s3:n1 s3:p3 s3:p3b s3:f1)

export RPS=50 ELEM_KB=256 WORK_N=64 EXTRA=hashlib,zlib
cache_base=64
probe_mib=2048
headroom_base=22
grow_kbps=250
f1_anon_mib=131

for t in "${targets[@]}"; do
  case "$t" in s3:n1|s3:p3|s3:p3b|s3:f1) ;; *) echo "unknown target: $t" >&2; usage; exit 2 ;; esac
done

mkdir -p "$results/environment"
rc=0
"$here/scripts/build.sh" >/dev/null

env_record() {
  {
    echo "kernel=$(uname -r)"
    echo "os=$(. /etc/os-release; echo "$PRETTY_NAME")"
    echo "docker=$(docker version --format '{{.Server.Version}}')"
    echo "docker_cgroup_driver=$(docker info --format '{{.CgroupDriver}}')"
    echo "lru_gen_enabled=$(cat /sys/kernel/mm/lru_gen/enabled 2>/dev/null || echo none)"
    echo "swapon=$(swapon --show --noheadings --raw | tr '\n' ';')"
    echo "rps=$RPS elem_kb=$ELEM_KB work_n=$WORK_N extra=$EXTRA"
  } > "$results/environment/$1.txt"
}

run() {
  local out=$1 mem=$2 cache=$3 mode=$4 dur=$5
  echo "== $(basename "$(dirname "$out")")/$(basename "$out"): --memory ${mem}MiB, cache ${cache}MiB, $mode, ${dur}s" >&2
  "$here/scripts/run.sh" "$out" "$mem" "$cache" "$mode" "$dur"
  [ "$mode" = probe ] || python3 "$here/scripts/analyze.py" "$out" >&2
}

nonfile() { python3 -c 'import json,sys;s=[json.loads(l) for l in open(sys.argv[1])][-1];print(round((s["current"]-s["stat"]["file"])/2**20,3))' "$1/cgroup_warm.jsonl"; }
calc() { python3 -c "import sys;print(round($1, 3))"; }
field() { python3 -c 'import json,sys;o=json.load(open(sys.argv[1]));v=o
for k in sys.argv[2].split("."): v=v[k]
print(v)' "$1/summary.json" "$2"; }
quarter() { python3 -c 'import sys;print(round(float(sys.argv[1])/0.25)*0.25)' "$1"; }

cond_job() {
  local c=$1 out start=$SECONDS nf mem cache_f1 size per
  out=$results/s3/$c
  rm -rf "$out"
  env_record "s3-$c"
  run "$out/probe" "$probe_mib" "$cache_base" probe 0
  nf=$(nonfile "$out/probe")
  mem=$(calc "$nf + $headroom_base")
  echo "probe_nonfile_mib=$nf memory_base_mib=$mem target_headroom_base_mib=$headroom_base elem_kb=$ELEM_KB" > "$out/sizes.txt"
  case "$c" in
    p3)
      echo "grow_kbps=$grow_kbps" >> "$out/sizes.txt"
      GROW_KBPS=$grow_kbps run "$out/p3" "$mem" "$cache_base" p3 180
      ;;
    p3b)
      per=$(python3 -c "print(round($ELEM_KB * $RPS / $grow_kbps))")
      echo "grow_per_requests=$per" >> "$out/sizes.txt"
      GROW_PER_REQUESTS=$per run "$out/p3b" "$mem" "$cache_base" p3b 180
      ;;
    n1)
      run "$out/base" "$mem" "$cache_base" base 30
      mkdir -p /var/tmp/mlr-fill
      size=$(( (${mem%.*} + 256) * 1048576 ))
      [ "$(stat -c %s /var/tmp/mlr-fill/fill.bin 2>/dev/null || echo 0)" -ge "$size" ] || head -c "$size" /dev/urandom > /var/tmp/mlr-fill/fill.bin
      run "$out/n1" "$mem" "$cache_base" n1 30
      ;;
    f1)
      run "$out/base" "$mem" "$cache_base" base 30
      cache_f1=$(quarter "$(calc "$f1_anon_mib - $(field "$out/base" anon_mib) + $cache_base")")
      echo "f1_target_anon_mib=$f1_anon_mib cache_f1_mib=$cache_f1 memory_f1_mib=$(calc "$mem + $cache_f1 - $cache_base")" >> "$out/sizes.txt"
      run "$out/f1" "$(calc "$mem + $cache_f1 - $cache_base")" "$cache_f1" base 30
      ;;
  esac
  echo "$((SECONDS - start))" > "$out/job_seconds.txt"
  python3 -c 'import glob,json,os,sys
for f in sorted(glob.glob(os.path.join(sys.argv[1], "*", "summary.json"))):
    sw = json.load(open(f)).get("swap") or {}
    if sw.get("max") != 0 or sw.get("current_max") != 0:
        print("s3-%s: %s: memory.swap.max %s, largest memory.swap.current %s" % (sys.argv[2], os.path.basename(os.path.dirname(f)), sw.get("max"), sw.get("current_max")))' "$out" "$c" > "$results/invalid-s3-$c.txt"
  [ -s "$results/invalid-s3-$c.txt" ] && rc=1 || rm -f "$results/invalid-s3-$c.txt"
}

for t in "${targets[@]}"; do
  cond_job "${t#s3:}"
done

python3 "$here/scripts/report.py" "$results" > "$results/summary.md"
cat "$results/summary.md"
echo "wrote $results/summary.md" >&2
exit "$rc"
