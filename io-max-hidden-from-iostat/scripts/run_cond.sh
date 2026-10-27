#!/usr/bin/env bash
set -uo pipefail
out=$1 name=$2 engine=$3 numjobs=$4 iodepth=$5 riops=$6 via=$7 target=$8 tdev=$9 pattern=${10} trace=${11} iodevs=${12} stop_slice_ms=${13}
here=$(cd "$(dirname "$0")" && pwd)
runtime=${RUNTIME:-10}
cgdir=/sys/fs/cgroup/iomax-test
majmin=$(lsblk -ndo MAJ:MIN "$tdev" | tr -d ' ')
mkdir -p "$out"
{
  printf 'name\t%s\nengine\t%s\nnumjobs\t%s\niodepth\t%s\nriops\t%s\nvia\t%s\n' "$name" "$engine" "$numjobs" "$iodepth" "$riops" "$via"
  printf 'target\t%s\nthrottle_dev\t%s\nthrottle_majmin\t%s\npattern\t%s\ntrace\t%s\nstop_slice_ms\t%s\n' "$target" "$tdev" "$majmin" "$pattern" "$trace" "$stop_slice_ms"
  for d in $iodevs; do printf 'rotational_%s\t%s\n' "$d" "$(cat /sys/block/$d/queue/rotational)"; done
  printf 'hz\t%s\n' "$(awk -F= '/^CONFIG_HZ=/ { print $2 }' "/boot/config-$(uname -r)")"
} > "$out/settings.txt"

fio_args=(--name="$name" --filename="$target" --rw=randread --bs=4k --direct=1
  --ioengine="$engine" --iodepth="$iodepth" --numjobs="$numjobs" --group_reporting
  --time_based --runtime="$runtime" --norandommap --randrepeat=0
  --lat_percentiles=1 --percentile_list=50:90:99:99.9 --output-format=json+)
[ "$pattern" = batch ] && fio_args+=(--iodepth_batch_submit="$iodepth" --thinktime=250000 --thinktime_blocks="$iodepth" --thinktime_blocks_type=issue)
printf '%s\n' "${fio_args[@]}" > "$out/fio_args.txt"

tdn=$(basename "$tdev")
logged() {
  printf '%s\n' "$2" >> "$out/$1"
  (cd "$out" && bash -c "$2")
}
if [ "$trace" = tool ]; then
  printf '%s\n' "blktrace -d $tdev -o trace" > "$out/blktrace_commands.txt"
  (cd "$out" && exec blktrace -d "$tdev" -o trace > blktrace.log 2>&1) &
  btpid=$!
  sleep 1
elif [ "$trace" = ftrace ]; then
  : > "$out/ftrace_commands.txt"
  logged ftrace_commands.txt "echo blk > /sys/kernel/tracing/current_tracer"
  logged ftrace_commands.txt "echo queue,complete,notify > /sys/block/$tdn/trace/act_mask"
  logged ftrace_commands.txt "echo 1 > /sys/block/$tdn/trace/enable"
  logged ftrace_commands.txt "echo 1 > /sys/kernel/tracing/tracing_on"
  printf '%s\n' "cat /sys/kernel/tracing/trace_pipe > ftrace.txt" >> "$out/ftrace_commands.txt"
  (cd "$out" && exec cat /sys/kernel/tracing/trace_pipe > ftrace.txt) &
  btpid=$!
  sleep 0.5
fi

unit=iomax-$name
case "$via" in
  cgroup)
    echo "$majmin riops=$riops" > "$cgdir/io.max"
    bash -c 'echo $$ > "$1/cgroup.procs"; shift; exec fio "$@"' sh "$cgdir" "${fio_args[@]}" --output="$out/fio.json" &
    fpid=$!
    cg=$cgdir
    ;;
  systemd|systemd-file)
    limit_path=$tdev
    [ "$via" = systemd-file ] && limit_path=$target
    systemd-run --unit="$unit" --collect --wait --quiet -p "IOReadIOPSMax=$limit_path $riops" \
      /usr/bin/fio "${fio_args[@]}" --output="$out/fio.json" > "$out/systemd-run.log" 2>&1 &
    fpid=$!
    printf 'IOReadIOPSMax=%s %s\n' "$limit_path" "$riops" > "$out/limit_source.txt"
    cg=
    for _ in $(seq 50); do
      c=$(systemctl show -P ControlGroup "$unit.service" 2>/dev/null)
      [ -n "$c" ] && [ -d "/sys/fs/cgroup$c" ] && { cg=/sys/fs/cgroup$c; break; }
      sleep 0.1
    done
    systemctl show -p IOReadIOPSMax "$unit.service" >> "$out/limit_source.txt"
    ;;
  docker)
    cid=$(docker run -d --name "$unit" --device "$tdev" --device-read-iops "$tdev:$riops" iomax-fio "${fio_args[@]}")
    fpid=
    printf -- '--device-read-iops %s:%s\n' "$tdev" "$riops" > "$out/limit_source.txt"
    docker inspect -f 'BlkioDeviceReadIOps={{json .HostConfig.BlkioDeviceReadIOps}}' "$cid" >> "$out/limit_source.txt"
    cg=
    for _ in $(seq 50); do
      [ -d "/sys/fs/cgroup/system.slice/docker-$cid.scope" ] && { cg=/sys/fs/cgroup/system.slice/docker-$cid.scope; break; }
      sleep 0.1
    done
    ;;
esac
sleep 0.3
if [ -z "$cg" ]; then
  echo "RUN INVALID: cgroup not found for $name" | tee "$out/invalid.txt"
fi
printf '%s\n' "${cg#/sys/fs/cgroup}" > "$out/cgroup_path.txt"
cat "$cg/io.max" > "$out/io.max.txt" 2>&1
devnames=$(for d in $iodevs; do printf '%s ' "$d"; done)
iostat -dxy $devnames 1 9 > "$out/iostat.txt" 2>&1 &
ipid=$!
pidstat -d -C '^fio$' 1 9 > "$out/pidstat.txt" 2>&1 &
ppid_=$!
python3 "$here/sample_cg.py" "$cg" "$majmin" 9 > "$out/cg_samples.tsv" 2> "$out/cg_samples.err" &
spid=$!

if [ -n "$fpid" ]; then
  wait "$fpid"; echo "fio_rc	$?" >> "$out/settings.txt"
else
  echo "fio_rc	$(docker wait "$cid")" >> "$out/settings.txt"
  docker logs "$cid" > "$out/fio.json" 2> "$out/docker.stderr"
  docker rm "$cid" > /dev/null
fi
wait "$ipid" "$ppid_" "$spid"
cat "$cg/io.stat" > "$out/io.stat.txt" 2>/dev/null || true
cat "$cg/io.pressure" > "$out/io.pressure.txt" 2>/dev/null || true
[ "$via" = cgroup ] && echo "$majmin riops=max" > "$cgdir/io.max"

if [ "$trace" = tool ]; then
  kill -INT "$btpid"; wait "$btpid"
  logged blktrace_commands.txt "blkparse -i trace | grep -c throtl" > "$out/blktrace_throtl_count.txt"
  (cd "$out" && blkparse -q -i trace -o parsed.txt > /dev/null 2>&1)
  awk '{ n[$6]++ } END { for (a in n) print a, n[a] }' "$out/parsed.txt" | sort > "$out/blktrace_actions.txt"
  grep -a 'throtl' "$out/parsed.txt" > "$out/throtl.txt" || true
  awk '$6 == "Q" || $6 == "C" { print $4, $6 }' "$out/parsed.txt" > "$out/qc_times.txt"
  rm -f "$out"/trace.blktrace.* "$out/parsed.txt"
elif [ "$trace" = ftrace ]; then
  logged ftrace_commands.txt "echo 0 > /sys/block/$tdn/trace/enable"
  sleep 0.5
  kill "$btpid"; wait "$btpid" 2>/dev/null
  logged ftrace_commands.txt "echo nop > /sys/kernel/tracing/current_tracer"
  cat /sys/kernel/tracing/per_cpu/cpu*/stats | awk '/overrun/ { s += $2 } END { print "overrun", s }' > "$out/ftrace_stats.txt"
  : > "$out/ftrace_extract.txt"
  for c in "grep 'throtl \[R\] bio\.' ftrace.txt | head -n 2" "grep -c 'throtl \[R\] bio\.' ftrace.txt"; do
    printf '$ %s\n' "$c" >> "$out/ftrace_extract.txt"
    (cd "$out" && bash -c "$c") >> "$out/ftrace_extract.txt"
  done
  grep -a 'throtl' "$out/ftrace.txt" > "$out/throtl.txt" || true
  awk '{ for (i = 1; i <= NF; i++) if ($i ~ /^[0-9]+\.[0-9]+:$/) { a = $(i + 2); if (a ~ /^[0-9]+,[0-9]+$/) a = $(i + 3); if (a == "Q" || a == "C") print substr($i, 1, length($i) - 1), a; break } }' "$out/ftrace.txt" > "$out/qc_times.txt"
  rm -f "$out/ftrace.txt"
fi
true
