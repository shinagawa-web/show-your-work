#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
bin=$here/.bin
mkdir -p "$bin"
case $(uname -m) in aarch64|arm64) arch=arm64 ;; x86_64) arch=amd64 ;; *) echo "unsupported arch" >&2; exit 1 ;; esac
if ! "$bin/kind" version 2>/dev/null | grep -q "$KIND_VERSION"; then
  curl -fsSLo "$bin/kind" "https://kind.sigs.k8s.io/dl/$KIND_VERSION/kind-linux-$arch"
  chmod +x "$bin/kind"
fi
if ! "$bin/kubectl" version --client 2>/dev/null | grep -q "$KUBECTL_VERSION"; then
  curl -fsSLo "$bin/kubectl" "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/$arch/kubectl"
  chmod +x "$bin/kubectl"
fi
docker rm -f "$PG" >/dev/null 2>&1 || true
"$bin/kind" delete cluster --name "$CLUSTER" 2>/dev/null || true
"$bin/kind" create cluster --name "$CLUSTER" --image "$KIND_NODE_IMAGE" --config "$here/k8s/kind.yaml" --wait 120s &
create=$!
docker build -q --build-arg GO_IMAGE="$GO_IMAGE" --build-arg BASE_IMAGE="$BASE_IMAGE" -t "$APP_IMAGE" "$here/app" &
build=$!
docker pull -q "grafana/k6@$K6_DIGEST"
docker tag "grafana/k6@$K6_DIGEST" "$K6_TAG"
docker pull -q "redis@$REDIS_DIGEST"
docker tag "redis@$REDIS_DIGEST" "$REDIS_TAG"
docker pull -q "$POSTGRES_IMAGE"
wait "$create"
docker update --cpuset-cpus 0-2 "$CLUSTER-control-plane"
docker run -d --name "$PG" --cpuset-cpus 3 --network kind -e POSTGRES_PASSWORD=pg "$POSTGRES_IMAGE"
wait "$build"
for img in "$APP_IMAGE" "$K6_TAG" "$REDIS_TAG"; do
  docker save "$img" | docker exec -i "$CLUSTER-control-plane" ctr -n k8s.io images import -
done
docker exec "$CLUSTER-control-plane" sh -c 'rm -rf /k6out /stats && mkdir -m 777 /k6out /stats'
for i in $(seq 60); do docker exec "$PG" pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1 && break; sleep 1; done
docker exec -i "$PG" psql -v ON_ERROR_STOP=1 -U postgres -h 127.0.0.1 <<SQL
CREATE TABLE items (id int PRIMARY KEY, name text NOT NULL);
CREATE TABLE users (id int PRIMARY KEY, country text NOT NULL);
CREATE TABLE reviews (id bigserial PRIMARY KEY, item_id int NOT NULL, user_id int NOT NULL, score int NOT NULL);
INSERT INTO items SELECT g, 'item-' || g FROM generate_series(1, $KEYS) g;
INSERT INTO users SELECT g, (ARRAY['jp', 'us', 'de', 'fr', 'br'])[1 + g % 5] FROM generate_series(1, $USERS) g;
INSERT INTO reviews (item_id, user_id, score) SELECT i, 1 + (random() * ($USERS - 1))::int, (random() * 4)::int + 1 FROM generate_series(1, $KEYS) i, generate_series(1, $JOIN_ROWS) j;
CREATE INDEX ON reviews (item_id);
VACUUM ANALYZE;
SQL
