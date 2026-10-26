CREATE TABLE items (
  id    integer PRIMARY KEY,
  stock integer NOT NULL
);

CREATE TABLE orders (
  id         bigserial PRIMARY KEY,
  item_id    integer NOT NULL,
  status     text NOT NULL DEFAULT 'confirmed',
  created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO items (id, stock) VALUES (1, 1000000);
