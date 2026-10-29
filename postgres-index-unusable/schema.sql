CREATE TABLE members (
  id integer PRIMARY KEY,
  code varchar(20) NOT NULL,
  email varchar(255) NOT NULL,
  payload text NOT NULL
);
INSERT INTO members
SELECT i,
       'C' || lpad(i::text, 7, '0'),
       'user' || i || '@example.com',
       repeat('x', 60)
FROM generate_series(1, :rows) AS i;
CREATE INDEX members_code_idx ON members (code);
CREATE INDEX members_email_idx ON members (email);
VACUUM ANALYZE members;
