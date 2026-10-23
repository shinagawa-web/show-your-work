#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
out=$1 jdk=$2 docker_extra=$3 java_extra=$4 after=$5
peak_rate=${PEAK_RATE:-1000}
after_rate=${AFTER_RATE:-200}
peak_s=${PEAK_S:-20}
after_s=${AFTER_S:-60}
obj=${OBJ_SIZE:-64}
lifetime_ms=${LIFETIME_MS:-10}
[ "$after" = idle ] && after_rate=0
[ "$docker_extra" = - ] && docker_extra=
[ "$java_extra" = - ] && java_extra=
image_var=JDK${jdk}_IMAGE
image=${!image_var}
mkdir -p "$out"; out=$(cd "$out" && pwd)
chmod 777 "$out"
if [ "$jdk" = 8 ]; then
  log_opts="-XX:+PrintCommandLineFlags -XX:+PrintGCDetails -XX:+PrintGCTimeStamps -XX:+PrintAdaptiveSizePolicy -XX:AdaptiveSizePolicyOutputInterval=1 -Xloggc:/out/gc.log"
elif [ "$jdk" = 11 ]; then
  log_opts="-XX:+PrintCommandLineFlags -Xlog:gc*,gc+ergo+heap=debug:file=/out/gc.log:uptime,level,tags"
else
  log_opts="-XX:+PrintCommandLineFlags -Xlog:gc*,gc+ergo+heap=debug,gc+periodic=debug:file=/out/gc.log:uptime,level,tags"
fi
cmd="java -version > /out/java-version.txt 2>&1; date +%s.%N > /out/java-start.txt; java -Xmx1500m $java_extra $log_opts -cp /app Alloc $peak_rate $peak_s $after_rate $after_s $obj $lifetime_ms > /out/stdout.txt 2>&1; echo \"java exit \$?\" >> /out/stdout.txt; sleep 3"
name=g1h-$$
printf 'image=%s\ndocker_opts=--memory=2g --memory-swap=2g --cpus=2 --init %s\njava_opts=-Xmx1500m %s %s\nprogram_args=%s %s %s %s %s %s\n' \
  "$image" "$docker_extra" "$java_extra" "$log_opts" "$peak_rate" "$peak_s" "$after_rate" "$after_s" "$obj" "$lifetime_ms" > "$out/settings.txt"
t0=$(date +%s.%N); echo "$t0" > "$out/t0.txt"
id=$(docker run -d --name "$name" --memory=2g --memory-swap=2g --cpus=2 --init $docker_extra \
  -v "$out:/out" -v "${RESULTS:-$here/results}/app:/app:ro" "$image" sh -c "$cmd") || exit 1
cg=/sys/fs/cgroup/system.slice/docker-$id.scope
for _ in $(seq 50); do [ -e "$cg/memory.current" ] && break; sleep 0.1; done
{ echo "memory.max $(cat "$cg/memory.max")"; echo "memory.swap.max $(cat "$cg/memory.swap.max")"; echo "cpu.max $(cat "$cg/cpu.max")"; } > "$out/cgroup.txt"
python3 "$here/scripts/sample.py" "$cg" "$out/memory.tsv" "$t0" &
sampler=$!
docker wait "$name" > "$out/container-exit.txt"
wait "$sampler"
docker rm -f "$name" > /dev/null
exit 0
