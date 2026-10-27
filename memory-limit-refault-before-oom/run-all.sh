#!/usr/bin/env bash
set -euo pipefail
usage() {
  cat >&2 <<'EOF2'
usage: ./run-all.sh [target ...]

targets (default: s3:n1 s3:p1 s3:p2 s3:p3 s3:f1 s3:r1):
  s3:n1 s3:p1 s3:p2 s3:f1 s3:r1
                            a baseline, then the condition
  s3:p3                     anonymous memory grows under load from the baseline until OOM kill or 180 s

env:
  RESULTS=results           output directory

The run ends by writing $RESULTS/summary.md from everything found in $RESULTS.
EOF2
}
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
case "${1:-}" in -h|--help) usage; exit 0 ;; esac
targets=("$@")
[ ${#targets[@]} -gt 0 ] || targets=(s3:n1 s3:p1 s3:p2 s3:p3 s3:f1 s3:r1)

export RPS=50 ELEM_KB=256 WORK_N=64 EXTRA=hashlib,zlib
cache_base=64
probe_mib=2048
headroom_base=22
headroom_p1=8.8
p1_tries=3
r1_read_ahead_kb=4
grow_seconds=90

for t in "${targets[@]}"; do
  case "$t" in s3:n1|s3:p1|s3:p2|s3:p3|s3:f1|s3:r1) ;; *) echo "unknown target: $t" >&2; usage; exit 2 ;; esac
done

mkdir -p "$results/environment"
"$here/scripts/build.sh" >/dev/null

env_record() {
  {
    echo "host=$(hostname)"
    echo "cpu_model=$(lscpu | sed -n 's/^Model name: *//p' | head -1)"
    echo "nproc=$(nproc)"
    echo "kernel=$(uname -r)"
    echo "os=$(. /etc/os-release; echo "$PRETTY_NAME")"
    echo "docker=$(docker version --format '{{.Server.Version}}')"
    echo "docker_cgroup_driver=$(docker info --format '{{.CgroupDriver}}')"
    echo "lru_gen_enabled=$(cat /sys/kernel/mm/lru_gen/enabled 2>/dev/null || echo none)"
    echo "read_ahead_kb_path=$("$here/scripts/readahead.sh")"
    echo "read_ahead_kb=$(cat "$("$here/scripts/readahead.sh")")"
    echo "swapon=$(swapon --show --noheadings --raw | tr '\n' ';')"
    echo "memory_psi=$(tr '\n' ' ' < /proc/pressure/memory)"
    echo "docker_root_device=$(findmnt -no SOURCE,FSTYPE --target "$(docker info --format '{{.DockerRootDir}}')" | head -1)"
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
extra_for() { python3 -c 'import sys;print(round((float(sys.argv[1])-float(sys.argv[2]))/0.25)*0.25)' "$1" "$2"; }

cond_job() {
  local c=$1 out start=$SECONDS orig nf mem hb extra target try verdict name ra size
  out=$results/s3/$c
  rm -rf "$out"
  env_record "s3-$c"
  run "$out/probe" "$probe_mib" "$cache_base" probe 0
  nf=$(nonfile "$out/probe")
  mem=$(calc "$nf + $headroom_base")
  echo "probe_nonfile_mib=$nf memory_base_mib=$mem target_headroom_base_mib=$headroom_base target_headroom_p1_mib=$headroom_p1 elem_kb=$ELEM_KB" > "$out/sizes.txt"
  if [ "$c" = p3 ]; then
    GROW_KBPS=$(calc "$headroom_base * 1024 / $grow_seconds") run "$out/p3" "$mem" "$cache_base" p3 180
    echo "$((SECONDS - start))" > "$out/job_seconds.txt"
    return
  fi
  run "$out/base" "$mem" "$cache_base" base 30
  hb=$(field "$out/base" headroom_mib.median)
  target=$headroom_p1
  if { [ "$c" = p2 ] || [ "$c" = r1 ]; } && [ -f "$results/s3/p1/p1_final.txt" ]; then
    target=$(sed -n 's/^target_headroom_mib=//p' "$results/s3/p1/p1_final.txt")
  fi
  extra=$(extra_for "$hb" "$target")
  echo "base_headroom_mib=$hb p1_target_headroom_mib=$target extra_cache_mib=$extra cache_p1_mib=$(calc "$cache_base + $extra") memory_f1_mib=$(calc "$mem + $extra")" >> "$out/sizes.txt"
  case "$c" in
    n1)
      mkdir -p /var/tmp/mlr-fill
      size=$(( (${mem%.*} + 256) * 1048576 ))
      [ "$(stat -c %s /var/tmp/mlr-fill/fill.bin 2>/dev/null || echo 0)" -ge "$size" ] || head -c "$size" /dev/urandom > /var/tmp/mlr-fill/fill.bin
      run "$out/n1" "$mem" "$cache_base" n1 30
      ;;
    p1)
      name=p1
      for try in $(seq 0 $((p1_tries - 1))); do
        [ "$try" = 0 ] || name=p1-adj$try
        run "$out/$name" "$mem" "$(calc "$cache_base + $extra")" base 30
        verdict=$(python3 -c 'import json,sys;o=json.load(open(sys.argv[1]));b=json.load(open(sys.argv[2]))
over=o["latency_ms"]["p99"]>1.5*b["latency_ms"]["p99"];few=o["timeouts"]<=0.01*o["n_requests"]
print("ok" if over and few else ("more" if not over else "less"))' "$out/$name/summary.json" "$out/base/summary.json")
        echo "run=$name target_headroom_mib=$target extra_cache_mib=$extra verdict=$verdict" >> "$out/p1_tries.txt"
        [ "$verdict" != ok ] || break
        if [ "$verdict" = more ]; then target=$(calc "$target - 0.25"); else target=$(calc "$target + 0.25"); fi
        extra=$(extra_for "$hb" "$target")
      done
      printf 'run=%s\ntarget_headroom_mib=%s\nverdict=%s\n' "$name" "$(sed -n "s/^run=$name target_headroom_mib=\([^ ]*\).*/\1/p" "$out/p1_tries.txt")" "$verdict" > "$out/p1_final.txt"
      [ "$verdict" = ok ] || { echo "P1 is not slow-but-serving after $p1_tries runs; see $out/p1_tries.txt" >&2; rc=1; }
      ;;
    p2)
      orig=$(cat /sys/kernel/mm/lru_gen/enabled)
      trap 'echo "$orig" | sudo -n tee /sys/kernel/mm/lru_gen/enabled >/dev/null' EXIT
      if [ "$orig" = 0x0000 ]; then echo y; else echo n; fi | sudo -n tee /sys/kernel/mm/lru_gen/enabled >/dev/null
      echo "lru_gen_enabled_before=$orig after=$(cat /sys/kernel/mm/lru_gen/enabled)" > "$out/lru_switch.txt"
      run "$out/p2" "$mem" "$(calc "$cache_base + $extra")" base 30
      echo "$orig" | sudo -n tee /sys/kernel/mm/lru_gen/enabled >/dev/null
      trap - EXIT
      echo "restored=$(cat /sys/kernel/mm/lru_gen/enabled)" >> "$out/lru_switch.txt"
      ;;
    r1)
      ra=$("$here/scripts/readahead.sh")
      orig=$(cat "$ra")
      trap 'echo "$orig" | sudo -n tee "$ra" >/dev/null' EXIT
      echo "$r1_read_ahead_kb" | sudo -n tee "$ra" >/dev/null
      echo "path=$ra before=$orig after=$(cat "$ra")" > "$out/read_ahead_switch.txt"
      run "$out/r1" "$mem" "$(calc "$cache_base + $extra")" base 30
      echo "$orig" | sudo -n tee "$ra" >/dev/null
      trap - EXIT
      echo "restored=$(cat "$ra")" >> "$out/read_ahead_switch.txt"
      ;;
    f1) run "$out/f1" "$(calc "$mem + $extra")" "$(calc "$cache_base + $extra")" base 30 ;;
  esac
  echo "$((SECONDS - start))" > "$out/job_seconds.txt"
}

rc=0
for t in "${targets[@]}"; do
  cond_job "${t#s3:}"
done

python3 "$here/scripts/report.py" "$results" > "$results/summary.md"
cat "$results/summary.md"
echo "wrote $results/summary.md" >&2
exit "$rc"
