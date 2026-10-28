CREATE TABLE members (
  id integer PRIMARY KEY,
  fixed_code char(10) NOT NULL,
  code varchar(20) NOT NULL,
  email varchar(255) NOT NULL,
  amount numeric(12,2) NOT NULL,
  status varchar(16) NOT NULL,
  payload text NOT NULL
);
INSERT INTO members
SELECT i,
       'F' || lpad(i::text, 7, '0'),
       'C' || lpad(i::text, 7, '0'),
       'user' || i || '@example.com',
       i,
       CASE WHEN i % 10000 = 0 THEN 'locked' ELSE 'active' END,
       repeat('x', 60)
FROM generate_series(1, :rows) AS i;
CREATE INDEX members_fixed_code_idx ON members (fixed_code);
CREATE INDEX members_code_idx ON members (code);
CREATE INDEX members_email_idx ON members (email);
CREATE INDEX members_amount_idx ON members (amount);
CREATE INDEX members_status_idx ON members (status);
VACUUM ANALYZE members;
