#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import time
from decimal import Decimal

import psycopg

part, out = sys.argv[1], sys.argv[2]
SECTIONS = {"explain": "2", "causes": "3"}
job = f"{SECTIONS[part]} ({part})"
data = {"job": job, "part": part}
invalid = []

LIKE = "SELECT * FROM members WHERE code LIKE 'C000004%'"
LOWER = "SELECT * FROM members WHERE lower(email) = 'user42@example.com'"


def conninfo(db="lab", app="lab"):
    return f"host=127.0.0.1 port=15432 dbname={db} user=postgres password=test application_name={app}"


def compose(*args, stdin=None):
    p = subprocess.run(["docker", "compose", "exec", "-T", "postgres", *args], capture_output=True, text=True, input=stdin)
    return (p.stdout + p.stderr).rstrip()


def psql(*commands, db="lab"):
    args = []
    for c in commands:
        args += ["-c", c]
    return compose("psql", "-U", "postgres", "-d", db, *args)


def sh(cmd):
    return compose("sh", "-c", cmd)


def error_text(e):
    d = e.diag
    s = f"{d.severity}:  {d.message_primary}"
    if d.message_hint:
        s += f"\nHINT:  {d.message_hint}"
    return s


def uses_index(plan):
    return "Index Cond:" in plan and "Seq Scan" not in plan


class Session:
    def __init__(self, db="lab", app="lab"):
        self.conn = psycopg.connect(conninfo(db, app), autocommit=True, prepare_threshold=None)

    def x(self, sql):
        cur = self.conn.execute(sql)
        return cur.fetchall() if cur.description else []

    def prepare(self, setup, settings):
        self.x("DEALLOCATE ALL")
        for s in settings:
            self.x(s)
        for s in setup:
            self.x(s)

    def reset(self):
        self.x("RESET enable_seqscan")

    def explain(self, setup, query, settings=(), options=""):
        try:
            self.prepare(setup, settings)
            return "\n".join(r[0] for r in self.x(f"EXPLAIN {options}{query}"))
        except psycopg.Error as e:
            return error_text(e)
        finally:
            self.reset()


def sql_text(setup, query, settings=(), options=""):
    return "\n".join([f"{s};" for s in settings] + [f"{s};" for s in setup] + [f"EXPLAIN {options}{query};"])


def case(s, key, setup, query, off=True):
    r = {"sql": sql_text(setup, query), "query": query, "plan": s.explain(setup, query)}
    if off:
        r["plan_off"] = s.explain(setup, query, ["SET enable_seqscan = off"])
    data[key] = r
    print(f"== {key}\n{r['plan']}\n-- enable_seqscan = off\n{r.get('plan_off')}\n", flush=True)


BASES = [
    ("integer column = integer parameter", ["PREPARE q(integer) AS SELECT * FROM members WHERE id = $1"], "EXECUTE q(42)"),
    ("char(10) column = bpchar parameter", ["PREPARE q(bpchar) AS SELECT * FROM members WHERE fixed_code = $1"],
     "EXECUTE q('F0000042')"),
    ("varchar column = text parameter", ["PREPARE q(text) AS SELECT * FROM members WHERE code = $1"],
     "EXECUTE q('C0000042')"),
    ("varchar column, the range that LIKE 'C000004%' stands for, default collation index", [],
     "SELECT * FROM members WHERE code >= 'C000004' AND code < 'C000005'"),
    ("varchar email column = text", [], "SELECT * FROM members WHERE email = 'user42@example.com'"),
]


def bases(s):
    data["bases"] = []
    for title, setup, query in BASES:
        plan = s.explain(setup, query)
        data["bases"].append({"title": title, "sql": sql_text(setup, query), "plan": plan})
        if not uses_index(plan):
            invalid.append(f"job `{job}`: base query does not use an index: {title}")


def describe(db="lab"):
    return psql(r"\d members", db=db)


def databases():
    return psql(r"\l")


def header(s):
    v = s.x("SELECT version()")[0][0]
    size = s.x("SELECT pg_size_pretty(pg_relation_size('members')), current_setting('min_parallel_table_scan_size'), "
               "current_setting('max_parallel_workers_per_gather')")[0]
    with open(os.path.join(out, "version.txt"), "w") as f:
        f.write(v + "\n")
    data["setup"] = {"version": v, "heap_size": size[0], "min_parallel_table_scan_size": size[1],
                     "max_parallel_workers_per_gather": size[2]}


def part_explain(s):
    data["describe"] = describe()
    case(s, "p1_numeric", ["PREPARE q(numeric) AS SELECT * FROM members WHERE id = $1"], "EXECUTE q(42)")
    case(s, "p1_double", ["PREPARE q(double precision) AS SELECT * FROM members WHERE id = $1"], "EXECUTE q(42)")
    case(s, "p5", ["PREPARE q(text) AS SELECT * FROM members WHERE fixed_code = $1"], "EXECUTE q('F0000042')")
    case(s, "p6", ["PREPARE q(text) AS SELECT * FROM members WHERE code = $1"], "EXECUTE q('C0000042')", off=False)
    case(s, "p7_default", [], LIKE)
    case(s, "p8", [], LOWER)
    case(s, "p9", [], "SELECT * FROM members WHERE status = 'active'")
    data["status_counts"] = psql("SELECT status = 'active' AS is_active, count(*) FROM members GROUP BY 1 ORDER BY 1")
    case(s, "p10_p1", ["PREPARE q(numeric) AS SELECT id FROM members WHERE id = $1"], "EXECUTE q(42)")
    case(s, "p10_p7", [], "SELECT code FROM members WHERE code LIKE 'C000004%'")
    case(s, "p10_p8", [], "SELECT email FROM members WHERE lower(email) = 'user42@example.com'")
    generic_plan(s)


def part_causes(s):
    data["describe"] = describe()
    case(s, "p2", ["PREPARE q(bigint) AS SELECT * FROM members WHERE id = $1"], "EXECUTE q(42)", off=False)
    case(s, "p3", ["PREPARE q(varchar) AS SELECT * FROM members WHERE id = $1"], "EXECUTE q('42')", off=False)
    case(s, "p4", ["PREPARE q(varchar) AS SELECT * FROM members WHERE fixed_code = $1"], "EXECUTE q('F0000042')",
         off=False)
    case(s, "p5", ["PREPARE q(text) AS SELECT * FROM members WHERE fixed_code = $1"], "EXECUTE q('F0000042')")

    data["collations"] = psql("SELECT collname, collprovider, collcollate, collctype, colllocale FROM pg_collation "
                              "WHERE collname IN ('C', 'C.utf8', 'pg_c_utf8') ORDER BY collname")
    data["databases"] = databases()
    variants = [
        ("default collation (base)", "CREATE INDEX members_code_idx ON members (code)", "members_code_idx"),
        ("text_pattern_ops", "CREATE INDEX members_code_pattern_idx ON members (code text_pattern_ops)",
         "members_code_pattern_idx"),
        ('COLLATE "C"', 'CREATE INDEX members_code_c_idx ON members (code COLLATE "C")', "members_code_c_idx"),
        ('libc COLLATE "C.utf8"', 'CREATE INDEX members_code_c_utf8_idx ON members (code COLLATE "C.utf8")',
         "members_code_c_utf8_idx"),
        ('builtin COLLATE "pg_c_utf8"',
         'CREATE INDEX members_code_pg_c_utf8_idx ON members (code COLLATE "pg_c_utf8")',
         "members_code_pg_c_utf8_idx"),
    ]
    s.x("DROP INDEX members_code_idx")
    data["p7"] = []
    for label, ddl, name in variants:
        try:
            s.x(ddl)
        except psycopg.Error as e:
            data["p7"].append({"label": label, "ddl": ddl, "error": error_text(e)})
            continue
        r = {"label": label, "ddl": ddl}
        r["indexdef"] = s.x(f"SELECT indexdef FROM pg_indexes WHERE indexname = '{name}'")[0][0]
        r["plan"] = s.explain([], LIKE)
        if not uses_index(r["plan"]):
            r["plan_off"] = s.explain([], LIKE, ["SET enable_seqscan = off"])
        data["p7"].append(r)
        print(f"== P7 {label}\n{r['indexdef']}\n{r['plan']}\n", flush=True)
        s.x(f"DROP INDEX {name}")
    s.x("CREATE INDEX members_code_idx ON members (code)")

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "django_ddl.py")) as f:
        src = f.read()
    p = subprocess.run([sys.executable, os.path.join(here, "django_ddl.py")], capture_output=True, text=True)
    data["p12"] = {"model": src[src.index("class Member"):src.index("with connection")].rstrip(),
                   "ddl": (p.stdout + p.stderr).rstrip()}
    print(f"== P12\n{data['p12']['ddl']}\n", flush=True)
    if p.returncode != 0:
        invalid.append(f"job `{job}`: scripts/django_ddl.py exited with {p.returncode}")
    driver_and_dump(s)


def log_lines(app):
    time.sleep(0.5)
    p = subprocess.run(["docker", "compose", "logs", "--no-log-prefix", "postgres"], capture_output=True, text=True)
    return [l for l in p.stdout.splitlines() if f" app={app} " in l]


def driver_and_dump(s):
    data["p14"] = {"psycopg": psycopg.__version__, "libpq": psycopg.pq.version(), "impl": psycopg.pq.__impl__,
                   "values": []}
    for label, value, pgtype in [("decimal", Decimal("42"), "numeric"), ("float", 42.0, "double precision")]:
        app = f"p14-{label}"
        with psycopg.connect(conninfo(app=app), autocommit=True) as c:
            c.execute("SELECT * FROM members WHERE id = %s", (value,)).fetchall()
        lines = log_lines(app)
        r = {"label": label, "value": repr(value), "pgtype": pgtype, "app": app, "log": lines}
        data["p14"]["values"].append(r)
        stmt = param = None
        for i, l in enumerate(lines):
            m = re.search(r"execute <unnamed>: (.*)$", l)
            if m:
                stmt = m.group(1)
                if i + 1 < len(lines):
                    pm = re.search(r"Parameters: \$1 = (.*)$", lines[i + 1])
                    param = pm.group(1) if pm else None
        if not stmt or not param:
            invalid.append(f"job `{job}`: no execute line with parameters in the log for {app}")
            continue
        pasted = stmt.replace("$1", param)
        r["pasted_sql"] = f"EXPLAIN {pasted};"
        r["pasted_plan"] = psql(f"EXPLAIN {pasted}")
        r["prepared_sql"] = f"PREPARE q({pgtype}) AS {stmt};\nEXPLAIN EXECUTE q({param});"
        r["prepared_plan"] = psql(f"PREPARE q({pgtype}) AS {stmt}", f"EXPLAIN EXECUTE q({param})")
        with psycopg.connect(conninfo(app=f"{app}-explain"), autocommit=True) as c:
            r["via_driver_plan"] = "\n".join(
                row[0] for row in c.execute("EXPLAIN SELECT * FROM members WHERE id = %s", (value,)))
        print(f"== P14 {label}\n" + "\n".join(lines) + f"\n{pasted}\n{r['via_driver_plan']}\n", flush=True)

    create = "CREATE DATABASE staging TEMPLATE template0 LOCALE_PROVIDER libc LOCALE 'C'"
    restore = "pg_dump -U postgres -t members lab | psql -q -U postgres -d staging"
    r = {"create": create, "restore": restore}
    r["create_out"] = psql(create)
    r["restore_out"] = sh(restore)
    r["analyze_out"] = psql("ANALYZE members", db="staging")
    r["databases"] = databases()
    r["describe_staging"] = describe("staging")
    r["sql"] = f"EXPLAIN {LIKE};"
    r["plan_lab"] = s.explain([], LIKE)
    st = Session(db="staging")
    r["plan_staging"] = st.explain([], LIKE)
    print(f"== P13 staging\n{r['plan_staging']}\n", flush=True)
    r["dump_create"] = sh("pg_dump -U postgres --create --schema-only -t members lab | grep -E '^(CREATE DATABASE|SET|SELECT pg_catalog.set_config)'")
    r["dump_plain_count"] = sh("pg_dump -U postgres --schema-only -t members lab | grep -cE '^CREATE DATABASE'")
    data["p13"] = r


def generic_plan(s):
    g = []
    for q in ["SELECT * FROM members WHERE id = $1"]:
        g.append({"sql": f"EXPLAIN (GENERIC_PLAN) {q};", "plan": s.explain([], q, options="(GENERIC_PLAN) ")})
    s.x("CREATE INDEX members_code_pattern_idx ON members (code text_pattern_ops)")
    for q, opt in [("SELECT * FROM members WHERE code LIKE $1", "(GENERIC_PLAN) "), (LIKE, "")]:
        g.append({"sql": f"EXPLAIN {opt}{q};", "plan": s.explain([], q, options=opt)})
    s.x("DROP INDEX members_code_pattern_idx")
    data["generic_plan"] = g


def main():
    s = Session()
    header(s)
    bases(s)
    {"explain": part_explain, "causes": part_causes}[part](s)
    with open(os.path.join(out, "data.json"), "w") as f:
        json.dump(data, f, indent=1)
    with open(os.path.join(out, "invalid.txt"), "a") as f:
        for r in invalid:
            f.write(r + "\n")
            print(f"RUN INVALID: {r}")
    sys.exit(1 if invalid else 0)


main()
