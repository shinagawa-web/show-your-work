#!/usr/bin/env bash
set -euo pipefail
for i in $(seq 60); do docker exec "$PG" pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1 && break; sleep 1; done
[ -z "$(docker exec "$PG" psql -U postgres -h 127.0.0.1 -Atc "SELECT to_regclass('items')" 2>/dev/null)" ] || exit 0
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
