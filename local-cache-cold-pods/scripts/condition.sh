#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
results=$RESULTS
cond=$1
shift
. "$here/scripts/versions.env"
. "$here/scripts/params.env"
eval "$("$here/scripts/cpus.sh")"
for a in "$@"; do case $a in [A-Z]*=*) eval "${a%%=*}=\${a#*=}" ;; *) echo "bad argument $a" >&2; exit 2 ;; esac; done
case $cond in
  app-restart-local) REDIS=none LOCAL_SIZE=$LOCAL_SIZE_FULL OP=app-restart WARM=$WARM_LOCAL ;;
  app-restart-redis) REDIS=single-rdb LOCAL_SIZE=0 OP=app-restart ;;
  redis-restart-none) REDIS=single-none LOCAL_SIZE=0 OP=redis-restart ;;
  redis-restart-none-probe) REDIS=single-none LOCAL_SIZE=0 OP=redis-restart READY_DELAY_SECONDS=1 READY_PERIOD_SECONDS=1 ;;
  redis-restart-rdb) REDIS=single-rdb LOCAL_SIZE=0 OP=redis-restart ;;
  redis-restart-rdb-probe) REDIS=single-rdb LOCAL_SIZE=0 OP=redis-restart READY_DELAY_SECONDS=1 READY_PERIOD_SECONDS=1 ;;
  sentinel-restart) REDIS=sentinel LOCAL_SIZE=0 OP=sentinel-restart ;;
  *) echo "unknown condition $cond" >&2; exit 2 ;;
esac
export PATH=$here/.bin:$PATH
export KIND_NODE_IMAGE APP_IMAGE K6_TAG CLUSTER PG NODE_CPUS PG_CPUS RATE START_RATE RAMP KEYS ZIPF_S LOCAL_SIZE LOCAL_TTL_SECONDS REDIS_TTL_SECONDS VALUE_BYTES READ_BYTES POOL_MAX JOIN_ROWS USERS WARM MAX_OBS QUIET_READS QUIET_SECONDS QUIET_DB_CPU K6_GOGC K6_MEMLIMIT PREALLOC_VUS MAX_VUS REDIS OP REDIS_TAG REUSE READY_DELAY_SECONDS READY_PERIOD_SECONDS
rm -rf "$results"
mkdir -p "$results" "$results/logs"
abort() { echo "RUN INVALID: $1" | tee "$results/summary.md"; exit 1; }
py() { python3 -B "$here/scripts/$@"; }
node=$CLUSTER-control-plane
{
  echo "cond=$cond redis=$REDIS op=$OP warm=$WARM"
  for v in NODE_CPUS PG_CPUS RATE START_RATE RAMP KEYS ZIPF_S LOCAL_SIZE LOCAL_TTL_SECONDS REDIS_TTL_SECONDS VALUE_BYTES READ_BYTES POOL_MAX JOIN_ROWS USERS MAX_OBS QUIET_READS QUIET_SECONDS QUIET_DB_CPU K6_GOGC K6_MEMLIMIT PREALLOC_VUS MAX_VUS READY_DELAY_SECONDS READY_PERIOD_SECONDS; do echo "$v=${!v}"; done
} > "$results/params.txt"

(
  for i in 1 2 3 4; do
    docker build -q --build-arg GO_IMAGE="$GO_IMAGE" --build-arg BASE_IMAGE="$BASE_IMAGE" -t "$APP_IMAGE" "$here/app" && exit 0
    sleep 3
  done
  exit 1
) > "$results/build.log" 2>&1 &
build=$!
"$here/scripts/setup.sh" > "$results/setup.log" 2>&1 || { tail -30 "$results/setup.log"; abort "setup.sh failed"; }
"$here/scripts/seed.sh" >> "$results/setup.log" 2>&1 &
seed=$!

redis_pods=
case $REDIS in
  single-*)
    redis_pods=redis-0
    kubectl create configmap redis-health --from-file="$here/k8s/redis_readiness.sh" --from-file="$here/k8s/redis_liveness.sh" >/dev/null || abort "configmap redis-health failed"
    py render.py redis "${REDIS#single-}" > "$results/redis.json"
    kubectl apply -f "$results/redis.json" >/dev/null || abort "apply of Redis failed"
    ;;
  sentinel)
    redis_pods="redis-ha-server-0 redis-ha-server-1 redis-ha-server-2"
    helm template redis-ha "$here/.bin/redis-ha-$REDIS_HA_CHART.tgz" -f "$here/k8s/redis-ha-values.yaml" > "$results/redis-ha-rendered.yaml"
    helm install redis-ha "$here/.bin/redis-ha-$REDIS_HA_CHART.tgz" -f "$here/k8s/redis-ha-values.yaml" > "$results/helm.log" 2>&1 || abort "helm install failed"
    ;;
esac

wait "$build" || { tail -30 "$results/build.log"; abort "build of the app failed"; }
docker save "$APP_IMAGE" | docker exec -i "$node" ctr -n k8s.io images import - >> "$results/build.log" 2>&1 || abort "import of the app image failed"
wait "$seed" || abort "seed failed"
pgip=$(docker inspect -f '{{.NetworkSettings.Networks.kind.IPAddress}}' "$PG")
py render.py pg "$pgip" | kubectl apply -f - >/dev/null 2>&1 || abort "apply of the pg Service failed"
q="SELECT i.id, i.name, count(r.id), avg(r.score)::float8, count(DISTINCT u.country) FROM items i JOIN reviews r ON r.item_id = i.id JOIN users u ON u.id = r.user_id WHERE i.id = :id GROUP BY i.id, i.name;"
docker run --rm --network kind --cpuset-cpus "$NODE_CPUS" -e PGPASSWORD=pg --entrypoint sh "$POSTGRES_IMAGE" -c \
  "printf '%s\n' '\\set id random(1, $KEYS)' '$q' > /tmp/q.sql && pgbench -n -h $pgip -U postgres -c 16 -j 4 -T 8 -f /tmp/q.sql postgres" \
  > "$results/pgbench.txt" 2>&1 || abort "pgbench failed"

py render.py app > "$results/app.json"
kubectl apply -f "$results/app.json" >/dev/null || abort "apply of the app failed"
kubectl rollout status deploy/app --timeout=180s >/dev/null || abort "app did not roll out"
kubectl wait --for=condition=Ready pod -l app=app --timeout=60s >/dev/null || abort "app Pods not Ready"
kubectl get pod -l app=app -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort > "$results/initial-pods.txt"
[ "$(wc -l < "$results/initial-pods.txt")" = 4 ] || abort "expected 4 app Pods before the load"

case $REDIS in
  single-*) kubectl rollout status statefulset/redis --timeout=150s >/dev/null || abort "Redis not Ready" ;;
esac

redis_state() {
  for p in $redis_pods; do
    echo "== $p"
    kubectl exec "$p" -c redis -- redis-cli dbsize 2>&1
    kubectl exec "$p" -c redis -- redis-cli info replication 2>&1 | grep -E '^(role|connected_slaves|master_link_status|master_replid|master_repl_offset)'
    kubectl exec "$p" -c redis -- redis-cli info persistence 2>&1 | grep -E '^(loading|rdb_last_load_keys_loaded|rdb_last_load_keys_expired|rdb_last_save_time)'
    kubectl exec "$p" -c redis -- redis-cli info memory 2>&1 | grep -E '^used_memory_human'
    kubectl exec "$p" -c redis -- sh -c 'ls -la /data/dump.rdb' 2>&1
  done
}
sentinel_master() {
  kubectl exec redis-ha-server-0 -c sentinel -- redis-cli -p 26379 sentinel get-master-addr-by-name mymaster 2>/dev/null | head -1
}
announce() { kubectl get svc "redis-ha-announce-$1" -o jsonpath='{.spec.clusterIP}'; }
if [ "$REDIS" = sentinel ]; then
  target=2
  for i in $(seq 200); do
    [ -n "$(sentinel_master)" ] && break
    sleep 0.5
  done
  if [ "$(sentinel_master)" != "$(announce "$target")" ]; then
    for i in $(seq 120); do
      [ "$(kubectl exec "redis-ha-server-$target" -c redis -- redis-cli info replication 2>/dev/null | tr -d '\r' | grep -c '^master_link_status:up')" = 1 ] && break
      sleep 0.5
    done
    for i in 0 1 2; do
      [ "$i" = "$target" ] || kubectl exec "redis-ha-server-$i" -c redis -- redis-cli config set replica-priority 0 >/dev/null
    done
    sleep 11
    for i in $(seq 20); do
      [ "$(kubectl exec redis-ha-server-0 -c sentinel -- redis-cli -p 26379 sentinel failover mymaster 2>/dev/null)" = OK ] && break
      sleep 1
    done
    for i in $(seq 40); do
      [ "$(sentinel_master)" = "$(announce "$target")" ] && break
      sleep 0.5
    done
    for i in 0 1 2; do
      kubectl exec "redis-ha-server-$i" -c redis -- redis-cli config set replica-priority 100 >/dev/null
    done
  fi
  [ "$(sentinel_master)" = "$(announce "$target")" ] || abort "redis-ha-server-$target did not become master"
  for i in $(seq 60); do
    [ "$(kubectl exec "redis-ha-server-$target" -c redis -- redis-cli info replication | tr -d '\r' | grep -c 'state=online')" = 2 ] && break
    sleep 0.5
  done
  kubectl rollout status statefulset/redis-ha-server --timeout=200s >/dev/null || abort "redis-ha not Ready"
fi

followers=()
for p in $redis_pods; do
  for c in redis ${REDIS%%-*}; do
    case $c in single) continue ;; esac
    (
      while :; do
        kubectl logs -f --timestamps "$p" -c "$c" >> "$results/logs/$p.$c.log" 2>/dev/null
        sleep 0.5
      done
    ) &
    followers+=($!)
  done
done

cg() { echo "/sys/fs/cgroup/system.slice/docker-$(docker inspect -f '{{.Id}}' "$1").scope"; }
pgcg=$(cg "$PG") nodecg=$(cg "$node")
[ -f "$pgcg/cpu.stat" ] && [ -f "$nodecg/cpu.stat" ] || abort "container cgroups not found ($pgcg, $nodecg)"
kubectl proxy --port=8001 > "$results/proxy.log" 2>&1 &
proxy=$!
for i in $(seq 50); do curl -fs http://127.0.0.1:8001/version >/dev/null && break; sleep 0.2; done
py poll.py "$results" "$pgcg" "$nodecg" > "$results/poll.log" 2>&1 &
poll=$!

kubectl create configmap k6-script --from-file=load.js="$here/k6/load.js" >/dev/null
py render.py k6 $(( WARM + MAX_OBS )) | kubectl apply -f - >/dev/null || abort "apply of the k6 Job failed"
t0=
for i in $(seq 600); do
  t0=$(docker exec "$node" sh -c 'grep -m1 "^T0 " /k6out/k6.log 2>/dev/null' | awk '{print $2}')
  [ -n "$t0" ] && break
  sleep 0.2
done
[ -n "$t0" ] || abort "k6 did not start"
echo "k6_t0 $t0" > "$results/timeline.txt"
python3 -c 'import sys, time; time.sleep(max(0, int(sys.argv[1]) / 1000 + int(sys.argv[2]) - time.time() - 3))' "$t0" "$WARM"
[ -z "$redis_pods" ] || redis_state > "$results/redis-before.txt" 2>&1
python3 -c 'import sys, time; time.sleep(max(0, int(sys.argv[1]) / 1000 + int(sys.argv[2]) - time.time()))' "$t0" "$WARM"
if [ "$OP" = sentinel-restart ]; then
  kubectl patch statefulset redis-ha-server -p '{"spec":{"updateStrategy":{"rollingUpdate":{"partition":2}}}}' > "$results/partition.txt" 2>&1 || abort "patch of the partition failed"
fi
op=$(date +%s.%N)
echo "op $op" >> "$results/timeline.txt"
case $OP in
  app-restart) kubectl rollout restart deploy/app > "$results/operation.txt" 2>&1 ;;
  redis-restart) kubectl rollout restart statefulset/redis > "$results/operation.txt" 2>&1 ;;
  sentinel-restart) kubectl rollout restart statefulset/redis-ha-server > "$results/operation.txt" 2>&1 ;;
esac
left=$(python3 -c 'import sys, time; print(max(1, int(float(sys.argv[1]) + int(sys.argv[2]) - time.time())))' "$op" "$MAX_OBS")
case $OP in
  app-restart) kubectl rollout status deploy/app --timeout=${left}s >> "$results/operation.txt" 2>&1 ;;
  redis-restart) kubectl rollout status statefulset/redis --timeout=${left}s >> "$results/operation.txt" 2>&1 ;;
  sentinel-restart)
    for i in $(seq $(( left * 2 ))); do
      grep -q 'ready to exit' "$results/logs/redis-ha-server-2.redis.log" && break
      sleep 0.5
    done
    ;;
esac
echo "rollout_done $(date +%s.%N) exit=$?" >> "$results/timeline.txt"
py watch.py "$results" "$node" "$op" >> "$results/timeline.txt"
k6pod=$(kubectl get pod -l job-name=k6 -o jsonpath='{.items[0].metadata.name}')
kubectl exec "$k6pod" -- kill -INT 1 >/dev/null 2>&1
echo "k6_interrupted $(date +%s.%N)" >> "$results/timeline.txt"
for i in $(seq 120); do
  phase=$(kubectl get pod "$k6pod" -o jsonpath='{.status.phase}')
  [ "$phase" = Succeeded ] || [ "$phase" = Failed ] && break
  sleep 1
done
code=$(kubectl get pod "$k6pod" -o jsonpath='{.status.containerStatuses[0].state.terminated.exitCode}')
case $code in 0|105) ;; *) echo "k6_incomplete $(date +%s.%N) exit=$code" >> "$results/timeline.txt" ;; esac
echo "k6_done $(date +%s.%N) exit=$code" >> "$results/timeline.txt"

pods() { kubectl get pod -l app=app -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort; }
pods > "$results/final-pods.txt"
[ -z "$redis_pods" ] || redis_state > "$results/redis-after.txt" 2>&1
if [ "$REDIS" = sentinel ]; then
  for i in 0 1 2; do echo "announce-$i $(announce "$i")"; done > "$results/sentinel-state.txt"
  kubectl exec redis-ha-server-0 -c sentinel -- redis-cli -p 26379 sentinel master mymaster >> "$results/sentinel-state.txt" 2>&1
fi

touch "$results/.stop"
wait "$poll"
rm -f "$results/.stop"
[ ${#followers[@]} -eq 0 ] || kill "${followers[@]}" 2>/dev/null
pkill -f "kubectl logs -f --timestamps" 2>/dev/null
kill "$proxy" 2>/dev/null

mkdir -p "$results/k6" "$results/stats"
docker exec "$node" tar -C /k6out -cf - . | tar -C "$results/k6" -xf -
docker exec "$node" tar -C /stats -cf - . | tar -C "$results/stats" -xf -
kubectl get pods -o wide > "$results/pods-final.txt" 2>&1
kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.uid} {.metadata.namespace}/{.metadata.name}{"\n"}{end}' > "$results/pod-uids.txt" 2>&1
kubectl get events --sort-by=.lastTimestamp > "$results/events.txt" 2>&1
for p in $(kubectl get pod -l app=app -o name); do kubectl logs "$p" > "$results/logs/${p#pod/}.log" 2>&1; done
docker stats --no-stream "$PG" "$node" > "$results/docker-stats-final.txt" 2>&1

py analyze.py "$results" "$cond" > "$results/summary.md" || abort "analyze.py failed"
cat "$results/summary.md"
! grep -q '^RUN INVALID' "$results/summary.md"
