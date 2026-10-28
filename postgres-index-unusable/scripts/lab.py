import json
import re
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import ClientCursor

DSN = "host=127.0.0.1 port=15432 user=postgres password=test dbname=lab"
ENTRY = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} \S+ \[\d+\] app=(\S*) ")
SCAN = re.compile(r"((?:Parallel )?(?:Seq Scan|Index Scan|Index Only Scan|Bitmap Heap Scan|Bitmap Index Scan)\b.*?)\s+\(cost=")
COND = re.compile(r"^\s*((?:Index Cond|Filter): .*)$")
PSQL_PREFIX = re.compile(r"^psql:[^:]*:\d+: ")
EXECUTE = re.compile(r"  (?:execute (\S+)|statement): (.*)$", re.S)
PARAM = re.compile(r"\$(\d+) = ('(?:[^']|'')*'|NULL)")

STATUS_SQL = "SELECT * FROM members WHERE status = %s"
ID_SQL = "SELECT * FROM members WHERE id = %s"
LIKE_LITERAL = "SELECT * FROM members WHERE code LIKE 'C000004%'"
LOWER_LITERAL = "SELECT * FROM members WHERE lower(email) = 'user42@example.com'"

BASE = [
    ("integer =", "SELECT * FROM members WHERE id = 42"),
    ("char(10) =", "SELECT * FROM members WHERE fixed_code = 'F0000042'"),
    ("varchar =", "SELECT * FROM members WHERE code = 'C0000042'"),
    ("numeric =", "SELECT * FROM members WHERE amount = 42.00"),
    ("range of the LIKE", "SELECT * FROM members WHERE code >= 'C000004' AND code < 'C000005'"),
    ("email =", "SELECT * FROM members WHERE email = 'user42@example.com'"),
    ("status = 'locked'", "SELECT * FROM members WHERE status = 'locked'"),
]

part, out = sys.argv[1], Path(sys.argv[2])
data = {"part": part, "cases": {}, "app": {}, "text": {}, "invalid": []}


def say(text):
    print(text, flush=True)


def run(args, stdin=""):
    p = subprocess.run(args, input=stdin, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def psql(sql, db="lab", aligned=False):
    args = ["docker", "compose", "exec", "-T", "postgres", "psql", "-X", "-q", "-U", "postgres", "-d", db]
    if not aligned:
        args += ["-A", "-t"]
    _, so, se = run(args, sql)
    text = so + se
    say(f"$ psql -d {db}\n{sql.strip()}\n{text}")
    return text


def parse_plan(text):
    lines = [PSQL_PREFIX.sub("", l) for l in text.splitlines()]
    return {
        "nodes": [m.group(1).replace("->", "").strip() for l in lines if (m := SCAN.search(l))],
        "conds": [m.group(1) for l in lines if (m := COND.match(l))],
        "disabled": any(l.strip() == "Disabled: true" for l in lines),
        "exec_time": next((l.split(":", 1)[1].strip() for l in lines if l.strip().startswith("Execution Time:")), None),
        "error": next((l.strip() for l in lines if l.startswith("ERROR:")), None),
    }


def explain(key, sql, pre="", db="lab", prefix="EXPLAIN (ANALYZE) "):
    text = psql(f"{pre}\n{prefix}{sql};\n", db)
    rec = parse_plan(text)
    rec.update(sql=sql, pre=pre.strip(), db=db, output=text)
    data["cases"][key] = rec
    return rec


def log_entries():
    _, so, _ = run(["docker", "compose", "logs", "--no-log-prefix", "postgres"])
    entries = []
    for line in so.splitlines():
        m = ENTRY.match(line)
        if m:
            entries.append([m.group(1), line])
        elif entries:
            entries[-1][1] += "\n" + line
    return entries


def app_log(app):
    es = []
    for _ in range(50):
        es = [e for a, e in log_entries() if a == app]
        if any("  plan:" in e and "FROM members WHERE" in e for e in es) and any(EXECUTE.search(e) and "FROM members WHERE" in e for e in es):
            break
        time.sleep(0.2)
    return es


def read_app_log(app):
    es = app_log(app)
    rec = {"execute": None, "detail": None, "name": None, "statement": None, "params": {}, "auto": None}
    for i, e in enumerate(es):
        m = EXECUTE.search(e)
        if m and "FROM members WHERE" in e and " parse " not in e and " bind " not in e:
            rec["execute"], rec["name"], rec["statement"] = e, m.group(1), m.group(2)
            if i + 1 < len(es) and "DETAIL:  Parameters:" in es[i + 1]:
                rec["detail"] = es[i + 1]
                rec["params"] = dict(PARAM.findall(es[i + 1]))
        if "  plan:" in e and "FROM members WHERE" in e:
            p = parse_plan(e)
            rec["auto"] = {"raw": e, "nodes": p["nodes"], "conds": p["conds"]}
    if rec["statement"] is not None:
        rec["psql"] = re.sub(r"\$(\d+)\b", lambda m: rec["params"].get(m.group(1), m.group(0)), rec["statement"])
    say(f"--- server log for {app}\n" + "\n".join(es))
    return rec


def prepared(conn):
    return [list(r) for r in conn.execute(
        "SELECT name, parameter_types::text, generic_plans, custom_plans FROM pg_prepared_statements WHERE name LIKE '\\_pg3\\_%' ORDER BY name",
        prepare=False).fetchall()]


def app_case(key, sql, value, client=False, prepare=None):
    app = key
    factory = ClientCursor if client else psycopg.Cursor
    with psycopg.connect(DSN, application_name=app, autocommit=True, cursor_factory=factory) as conn:
        cur = conn.execute(sql, (value,), prepare=prepare)
        rows = len(cur.fetchall())
        ps = prepared(conn) if prepare else None
    rec = read_app_log(app)
    rec.update(sql=sql, sent=repr(value), cursor="ClientCursor" if client else "default cursor", rows=rows, prepared=ps)
    data["app"][key] = rec
    if rec.get("psql"):
        explain(key + "-psql", rec["psql"])
    return rec


def baseline():
    data["base"] = []
    for label, sql in BASE:
        r = explain("base " + label, sql)
        ok = any("Index" in n for n in r["nodes"])
        data["base"].append([label, sql, ok])
        if not ok:
            data["invalid"].append(f"base query does not use an index: {sql}")


def environment():
    data["server_version"] = psql("SHOW server_version;").strip()
    data["psycopg"] = psycopg.__version__
    data["text"]["settings"] = psql(
        "SELECT name, setting FROM pg_settings WHERE name IN ('log_min_duration_statement','shared_preload_libraries','auto_explain.log_min_duration','plan_cache_mode','enable_seqscan','lc_collate') ORDER BY name;",
        aligned=True)
    data["text"]["datcollate"] = psql("SELECT datname, datlocprovider, datcollate FROM pg_database WHERE datname = 'lab';", aligned=True)
    data["text"]["counts"] = psql("SELECT status, count(*) FROM members GROUP BY status ORDER BY count(*) DESC;", aligned=True)


def part_explain():
    data["text"]["d"] = psql("\\d members", aligned=True)
    off = "SET enable_seqscan = off;"
    explain("p7-like", LIKE_LITERAL, pre=off)
    explain("p7-lower", LOWER_LITERAL, pre=off)
    explain("p7-status", "SELECT * FROM members WHERE status = 'active'", pre=off)
    explain("p7-p4", "SELECT * FROM members WHERE id = 42.0", pre=off)
    explain("p8", "SELECT * FROM members WHERE code = 'C0000042'::text")


def part_causes():
    data["p14"] = []
    for column, ptype, cond, value in [
        ("integer", "bigint", "id = $1", "42"),
        ("integer", "varchar", "id = $1", "'42'"),
        ("char(10)", "bpchar", "fixed_code = $1", "'F0000042'"),
        ("char(10)", "varchar", "fixed_code = $1", "'F0000042'"),
        ("char(10)", "text", "fixed_code = $1", "'F0000042'"),
        ("numeric", "numeric", "amount = $1", "42"),
        ("numeric", "double precision", "amount = $1", "42"),
    ]:
        pre = f"PREPARE q({ptype}) AS SELECT * FROM members WHERE {cond};"
        key = f"p14 {column} x {ptype}"
        explain(key, f"EXECUTE q({value})", pre=pre)
        data["p14"].append([column, ptype, pre, f"EXPLAIN (ANALYZE) EXECUTE q({value})", key])

    psql("DROP INDEX members_code_idx;")
    data["p9"] = []
    for label, cols in [
        ("default", "(code)"),
        ("text_pattern_ops", "(code text_pattern_ops)"),
        ("COLLATE \"C\"", "(code COLLATE \"C\")"),
        ("libc COLLATE \"C.utf8\"", "(code COLLATE \"C.utf8\")"),
        ("builtin COLLATE \"pg_c_utf8\"", "(code COLLATE \"pg_c_utf8\")"),
    ]:
        ddl = f"CREATE INDEX members_code_like_idx ON members {cols}"
        psql(ddl + ";")
        explain("p9 " + label, LIKE_LITERAL)
        psql("DROP INDEX members_code_like_idx;")
        data["p9"].append([label, ddl])
    psql("CREATE INDEX members_code_idx ON members (code);")

    data["text"]["pg_collation"] = psql(
        "SELECT collname, collprovider, collcollate, colllocale FROM pg_collation WHERE collname IN ('C', 'POSIX', 'C.utf8', 'pg_c_utf8', 'en_US.utf8') ORDER BY collname;",
        aligned=True)

    rc, so, se = run([sys.executable, str(Path(__file__).with_name("django_ddl.py"))])
    say(f"django_ddl.py rc={rc}\n{so}\n{se}")
    if rc != 0:
        data["invalid"].append("scripts/django_ddl.py failed")
    else:
        data["django"] = json.loads(so)


def part_log():
    app_case("base", ID_SQL, 42)
    app_case("p1-decimal", ID_SQL, Decimal("42"))
    app_case("p1-float", ID_SQL, 42.0)
    app_case("p2", ID_SQL, Decimal("42.00"))
    app_case("p3-like", "SELECT * FROM members WHERE code LIKE %s", "C000004%")
    app_case("p3-lower", "SELECT * FROM members WHERE lower(email) = %s", "user42@example.com")
    app_case("p3-status", STATUS_SQL, "active")
    app_case("p4", ID_SQL, 42.0, client=True)

    app_case("types-int", ID_SQL, 42, prepare=True)
    app_case("types-decimal", ID_SQL, Decimal("42"), prepare=True)
    app_case("types-float", ID_SQL, 42.0, prepare=True)

    explain("p11-numeric", "EXECUTE q('42')", pre="PREPARE q(numeric) AS SELECT * FROM members WHERE id = $1;")
    explain("p11-double", "EXECUTE q('42')", pre="PREPARE q(double precision) AS SELECT * FROM members WHERE id = $1;")

    rc, dump, err = run(["docker", "compose", "exec", "-T", "postgres", "pg_dump", "-U", "postgres", "-d", "lab", "-t", "members"])
    data["p6"] = {"pg_dump_rc": rc, "pg_dump_err": err, "dump_bytes": len(dump),
                  "dump_relation_stats_calls": dump.count("pg_restore_relation_stats"),
                  "dump_attribute_stats_calls": dump.count("pg_restore_attribute_stats")}
    psql("CREATE DATABASE staging LOCALE_PROVIDER libc LOCALE 'C' TEMPLATE template0;")
    rc, so, se = run(["docker", "compose", "exec", "-T", "postgres", "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "staging"], dump)
    data["p6"]["restore_rc"] = rc
    data["p6"]["restore_err"] = se
    say(f"restore rc={rc}\n{se}")
    lst = psql("\\l", aligned=True)
    data["text"]["l"] = lst
    lines = lst.splitlines()
    data["p6"]["l_lines"] = [l for l in lines if "Name" in l and "Owner" in l][:1] + [l for l in lines if re.match(r"^ (lab|staging)\s+\|", l)]
    stats_q = "SELECT relname, last_analyze, last_autoanalyze FROM pg_stat_user_tables WHERE relname = 'members';"
    data["p6"]["stat_before"] = psql(stats_q, db="staging", aligned=True)
    data["p6"]["pg_stats_before"] = psql("SELECT count(*) FROM pg_stats WHERE tablename = 'members';", db="staging").strip()
    explain("p6-before", LIKE_LITERAL, db="staging")
    psql("ANALYZE members;", db="staging")
    data["p6"]["stat_after"] = psql(stats_q, db="staging", aligned=True)
    data["p6"]["pg_stats_after"] = psql("SELECT count(*) FROM pg_stats WHERE tablename = 'members';", db="staging").strip()
    explain("p6-after", LIKE_LITERAL, db="staging")

    data["p12"] = []
    with psycopg.connect(DSN, application_name="p12-0", autocommit=True) as conn:
        for n, value in enumerate(["active"] * 10 + ["locked"], 1):
            app = f"p12-{n}"
            conn.execute(f"SET application_name = '{app}'", prepare=False)
            rows = len(conn.execute(STATUS_SQL, (value,)).fetchall())
            ps = prepared(conn)
            rec = read_app_log(app)
            rec.update(n=n, value=value, rows=rows, prepared=ps)
            data["p12"].append(rec)


def main():
    t0 = time.monotonic()
    environment()
    baseline()
    if not data["invalid"]:
        {"explain": part_explain, "causes": part_causes, "log": part_log}[part]()
    data["duration_s"] = round(time.monotonic() - t0, 1)
    (out / "data.json").write_text(json.dumps(data, indent=1))
    if data["invalid"]:
        (out / "invalid.txt").write_text("".join(f"RUN INVALID: {m}\n" for m in data["invalid"]))
        say("\n".join(data["invalid"]))
        sys.exit(1)


main()
