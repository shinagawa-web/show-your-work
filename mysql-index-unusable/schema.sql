SET SESSION cte_max_recursion_depth = 1000000;

CREATE TABLE members (
  id INT UNSIGNED NOT NULL PRIMARY KEY,
  code VARCHAR(20) NOT NULL,
  account_id INT NOT NULL,
  store_id VARCHAR(20) NOT NULL,
  email VARCHAR(255) NOT NULL,
  created_at DATETIME NOT NULL,
  KEY idx_code (code),
  KEY idx_account_id (account_id),
  KEY idx_store_id (store_id),
  KEY idx_email (email),
  KEY idx_created_at (created_at)
) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO members
WITH RECURSIVE seq (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < @rows)
SELECT n,
       CAST(1000000 + n AS CHAR),
       CEIL(n / 5),
       CAST(CEIL(n / 5) AS CHAR),
       CONCAT('user', n, '@example.com'),
       TIMESTAMP('2009-01-01 00:00:00') + INTERVAL n * 30 MINUTE
FROM seq;

CREATE TABLE stores (
  id INT UNSIGNED NOT NULL PRIMARY KEY,
  name VARCHAR(50) NOT NULL
) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO stores SELECT DISTINCT account_id, CONCAT('store ', account_id) FROM members;

CREATE TABLE b_mb4_unicode (
  id INT UNSIGNED NOT NULL PRIMARY KEY,
  member_code VARCHAR(20) NOT NULL,
  batch_id INT NOT NULL,
  payload VARCHAR(50) NOT NULL,
  KEY idx_member_code (member_code),
  KEY idx_batch_id (batch_id)
) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE b_mb3_unicode (
  id INT UNSIGNED NOT NULL PRIMARY KEY,
  member_code VARCHAR(20) NOT NULL,
  batch_id INT NOT NULL,
  payload VARCHAR(50) NOT NULL,
  KEY idx_member_code (member_code),
  KEY idx_batch_id (batch_id)
) DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

CREATE TABLE b_mb4_0900 (
  id INT UNSIGNED NOT NULL PRIMARY KEY,
  member_code VARCHAR(20) NOT NULL,
  batch_id INT NOT NULL,
  payload VARCHAR(50) NOT NULL,
  KEY idx_member_code (member_code),
  KEY idx_batch_id (batch_id)
) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE b_int (
  id INT UNSIGNED NOT NULL PRIMARY KEY,
  member_code INT NOT NULL,
  batch_id INT NOT NULL,
  payload VARCHAR(50) NOT NULL,
  KEY idx_member_code (member_code),
  KEY idx_batch_id (batch_id)
) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO b_mb4_unicode SELECT id, code, CEIL(id / 5), CONCAT('payload ', id) FROM members;
INSERT INTO b_mb3_unicode SELECT id, code, CEIL(id / 5), CONCAT('payload ', id) FROM members;
INSERT INTO b_mb4_0900 SELECT id, code, CEIL(id / 5), CONCAT('payload ', id) FROM members;
INSERT INTO b_int SELECT id, CAST(code AS UNSIGNED), CEIL(id / 5), CONCAT('payload ', id) FROM members;

ANALYZE TABLE members, stores, b_mb4_unicode, b_mb3_unicode, b_mb4_0900, b_int;
