#!/usr/bin/env bash
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
cond=${1:-}
case $cond in add-[0-9]*|restart-default|restart-surge1|redis) ;; *) echo "usage: run-all.sh add-<n>|restart-default|restart-surge1|redis [NAME=value ...]" >&2; exit 2 ;; esac
shift
. "$here/scripts/versions.env"
. "$here/scripts/params.env"
for a in "$@"; do case $a in [A-Z]*=*) eval "${a%%=*}=\${a#*=}" ;; *) echo "bad argument $a" >&2; exit 2 ;; esac; done
export PATH=$here/.bin:$PATH
export KIND_NODE_IMAGE APP_IMAGE K6_TAG REDIS_TAG CLUSTER PG RATE START_RATE RAMP KEYS ZIPF_S CACHE_SIZE CACHE_TTL_SECONDS REDIS_TTL_SECONDS POOL_MAX JOIN_ROWS USERS WARM MIN_READY MITIGATION_N K6_GOGC
start=$(date +%s)
rm -rf "$results"
mkdir -p "$results"
abort() { echo "RUN INVALID: $1" | tee "$results/summary.md"; exit 1; }
py() { python3 -B "$here/scripts/$@"; }
node=$CLUSTER-control-plane
mode=local strategy=default add=0
case $cond in
  add-*) add=${cond#add-}; obs=$(( add >= 8 ? 90 : 60 )) ;;
  redis) mode=redis add=$MITIGATION_N obs=$(( add >= 8 ? 90 : 60 )) ;;
  restart-default) obs=75 ;;
  restart-surge1) strategy=surge1 obs=$(( 4 * (MIN_READY + 5) + 25 )) ;;
esac
{
  echo "cond=$cond mode=$mode strategy=$strategy add=$add warm=$WARM obs=$obs"
  for v in RATE START_RATE RAMP KEYS ZIPF_S CACHE_SIZE CACHE_TTL_SECONDS REDIS_TTL_SECONDS POOL_MAX JOIN_ROWS USERS MIN_READY MITIGATION_N K6_GOGC; do echo "$v=${!v}"; done
} > "$results/params.txt"

"$here/scripts/setup.sh" > "$results/setup.log" 2>&1 || { tail -30 "$results/setup.log"; abort "setup.sh failed"; }
echo "setup $(( $(date +%s) - start ))s" > "$results/elapsed.txt"

pgip=$(docker inspect -f '{{.NetworkSettings.Networks.kind.IPAddress}}' "$PG")
py render.py pg "$pgip" | kubectl apply -f - >/dev/null 2>&1 || abort "apply of the pg Service failed"
q="SELECT i.id, i.name, count(r.id), avg(r.score)::float8, count(DISTINCT u.country) FROM items i JOIN reviews r ON r.item_id = i.id JOIN users u ON u.id = r.user_id WHERE i.id = :id GROUP BY i.id, i.name;"
docker run --rm --network kind --cpuset-cpus 0-2 -e PGPASSWORD=pg --entrypoint sh "$POSTGRES_IMAGE" -c \
  "printf '%s\n' '\\set id random(1, $KEYS)' '$q' > /tmp/q.sql && pgbench -n -h $pgip -U postgres -c 16 -j 4 -T 8 -f /tmp/q.sql postgres" \
  > "$results/pgbench.txt" 2>&1 || abort "pgbench failed"

if [ "$mode" = redis ]; then
  py render.py redis | kubectl apply -f - >/dev/null || abort "apply of redis failed"
  kubectl rollout status deploy/redis --timeout=120s >/dev/null || abort "redis did not roll out"
fi
py render.py app "$mode" "$strategy" > "$results/app.json"
kubectl apply -f "$results/app.json" >/dev/null || abort "apply of the app failed"
kubectl rollout status deploy/app --timeout=180s >/dev/null || abort "app did not roll out"
kubectl wait --for=condition=Ready pod -l app=app --timeout=60s >/dev/null || abort "app Pods not Ready"
kubectl get pod -l app=app -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort > "$results/initial-pods.txt"
[ "$(wc -l < "$results/initial-pods.txt")" = 4 ] || abort "expected 4 app Pods before the load"

{
  echo "\$ kind version"; kind version
  echo "\$ kubectl version"; kubectl version 2>&1
  echo "\$ uname -srm; nproc"; uname -srm; nproc
  echo "\$ docker inspect cpuset ($node, $PG)"; docker inspect -f '{{.Name}} {{.HostConfig.CpusetCpus}} {{.HostConfig.NanoCpus}}' "$node" "$PG"
  echo "\$ grep Cpus_allowed_list /proc/1/status (in $node)"; docker exec "$node" grep Cpus_allowed_list /proc/1/status
  echo "\$ grep Cpus_allowed_list /proc/1/status (in $PG)"; docker exec "$PG" grep Cpus_allowed_list /proc/1/status
  echo "\$ postgres --version"; docker exec "$PG" postgres --version
  echo "\$ SHOW max_connections, shared_buffers"; docker exec "$PG" psql -U postgres -h 127.0.0.1 -Atc "SHOW max_connections" -c "SHOW shared_buffers"
  echo "\$ SELECT count(*) FROM items, users, reviews"; docker exec "$PG" psql -U postgres -h 127.0.0.1 -Atc "SELECT (SELECT count(*) FROM items), (SELECT count(*) FROM users), (SELECT count(*) FROM reviews)"
  echo "\$ EXPLAIN ANALYZE (id=1)"; docker exec "$PG" psql -U postgres -h 127.0.0.1 -Atc "EXPLAIN ANALYZE ${q/:id/1}"
  echo "\$ sysctl net.netfilter.nf_conntrack_max"; sysctl net.netfilter.nf_conntrack_max 2>&1
  echo "\$ kubectl get pods -o wide"; kubectl get pods -o wide
  echo "\$ kubectl get deploy app -o jsonpath='{.spec.strategy} {.spec.minReadySeconds}'"; kubectl get deploy app -o jsonpath='{.spec.strategy} {.spec.minReadySeconds}{"\n"}'
} > "$results/00-environment.txt" 2>&1

cg() { echo "/sys/fs/cgroup/system.slice/docker-$(docker inspect -f '{{.Id}}' "$1").scope"; }
pgcg=$(cg "$PG") nodecg=$(cg "$node")
[ -f "$pgcg/cpu.stat" ] && [ -f "$nodecg/cpu.stat" ] || abort "container cgroups not found ($pgcg, $nodecg)"
kubectl proxy --port=8001 > "$results/proxy.log" 2>&1 &
proxy=$!
for i in $(seq 50); do curl -fs http://127.0.0.1:8001/version >/dev/null && break; sleep 0.2; done
py poll.py "$results" "$pgcg" "$nodecg" > "$results/poll.log" 2>&1 &
poll=$!

kubectl create configmap k6-script --from-file=load.js="$here/k6/load.js" >/dev/null
py render.py k6 $(( WARM + obs )) | kubectl apply -f - >/dev/null || abort "apply of the k6 Job failed"
t0=
for i in $(seq 600); do
  t0=$(docker exec "$node" sh -c 'grep -m1 "^T0 " /k6out/k6.log 2>/dev/null' | awk '{print $2}')
  [ -n "$t0" ] && break
  sleep 0.2
done
[ -n "$t0" ] || abort "k6 did not start"
echo "k6_t0 $t0" > "$results/timeline.txt"
python3 -c 'import sys, time; time.sleep(max(0, int(sys.argv[1]) / 1000 + int(sys.argv[2]) - time.time()))' "$t0" "$WARM"
echo "op $(date +%s.%N)" >> "$results/timeline.txt"
case $cond in
  restart-*)
    kubectl rollout restart deploy/app > "$results/operation.txt" 2>&1
    kubectl rollout status deploy/app --timeout=${obs}s >> "$results/operation.txt" 2>&1
    echo "rollout_done $(date +%s.%N) exit=$?" >> "$results/timeline.txt"
    ;;
  *)
    kubectl scale deploy/app --replicas=$(( 4 + add )) > "$results/operation.txt" 2>&1
    ;;
esac
kubectl wait --for=condition=complete job/k6 --timeout=$(( obs + 120 ))s >/dev/null || echo "k6_incomplete $(date +%s.%N)" >> "$results/timeline.txt"
echo "k6_done $(date +%s.%N)" >> "$results/timeline.txt"

pods() { kubectl get pod -l app=app -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort; }
if [ "$add" -gt 0 ]; then
  kubectl scale deploy/app --replicas=4 >> "$results/operation.txt" 2>&1
  for i in $(seq 120); do [ "$(pods | wc -l)" = 4 ] && break; sleep 1; done
  echo "scaled_back $(date +%s.%N)" >> "$results/timeline.txt"
fi
pods > "$results/final-pods.txt"

touch "$results/.stop"
wait "$poll"
rm -f "$results/.stop"
kill "$proxy" 2>/dev/null

mkdir -p "$results/k6" "$results/stats" "$results/logs"
docker exec "$node" tar -C /k6out -cf - . | tar -C "$results/k6" -xf -
docker exec "$node" tar -C /stats -cf - . | tar -C "$results/stats" -xf -
kubectl get pods -o wide > "$results/pods-final.txt" 2>&1
kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.uid} {.metadata.namespace}/{.metadata.name}{"\n"}{end}' > "$results/pod-uids.txt" 2>&1
kubectl get events --sort-by=.lastTimestamp > "$results/events.txt" 2>&1
kubectl get deploy app -o json > "$results/deploy-final.json"
for p in $(kubectl get pod -l app=app -o name); do kubectl logs "$p" > "$results/logs/${p#pod/}.log" 2>&1; done
docker stats --no-stream "$PG" "$node" > "$results/docker-stats-final.txt" 2>&1
echo "total $(( $(date +%s) - start ))s" >> "$results/elapsed.txt"

py analyze.py "$results" "$cond" > "$results/summary.md" || abort "analyze.py failed"
cat "$results/summary.md"
! grep -q '^RUN INVALID' "$results/summary.md"
