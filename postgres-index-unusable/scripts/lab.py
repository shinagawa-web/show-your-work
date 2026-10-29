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

ID_SQL = "SELECT * FROM members WHERE id = %s"
ID_NUMERIC = "SELECT * FROM members WHERE id = 42.0"
LIKE_LITERAL = "SELECT * FROM members WHERE code LIKE 'C000004%'"
LOWER_LITERAL = "SELECT * FROM members WHERE lower(email) = 'user42@example.com'"

BASE = [
    "SELECT * FROM members WHERE id = 42",
    "SELECT * FROM members WHERE code = 'C0000042'",
    "SELECT * FROM members WHERE email = 'user42@example.com'",
]

part, out = sys.argv[1], Path(sys.argv[2])
data = {"part": part, "cases": {}, "app": {}, "invalid": []}


def say(text):
    print(text, flush=True)


def run(args, stdin=""):
    p = subprocess.run(args, input=stdin, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def psql(sql, aligned=False):
    args = ["docker", "compose", "exec", "-T", "postgres", "psql", "-X", "-q", "-U", "postgres", "-d", "lab"]
    if not aligned:
        args += ["-A", "-t"]
    _, so, se = run(args, sql)
    text = so + se
    say(f"$ psql\n{sql.strip()}\n{text}")
    return text


def parse_plan(text):
    lines = [PSQL_PREFIX.sub("", l) for l in text.splitlines()]
    return {
        "nodes": [m.group(1).replace("->", "").strip() for l in lines if (m := SCAN.search(l))],
        "conds": [m.group(1) for l in lines if (m := COND.match(l))],
        "exec_time": next((l.split(":", 1)[1].strip() for l in lines if l.strip().startswith("Execution Time:")), None),
        "error": next((l.strip() for l in lines if l.startswith("ERROR:")), None),
    }


def explain(key, sql, pre=""):
    text = psql(f"{pre}\nEXPLAIN (ANALYZE) {sql};\n")
    rec = parse_plan(text)
    rec.update(sql=sql, pre=pre.strip(), output=text)
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


def is_statement(e):
    m = EXECUTE.search(e)
    return bool(m) and m.group(2).startswith("SELECT * FROM members WHERE")


def is_plan(e):
    return "  plan:" in e and "FROM members WHERE" in e


def read_app_log(app):
    es = []
    for _ in range(50):
        es = [e for a, e in log_entries() if a == app]
        if any(is_plan(e) for e in es) and any(is_statement(e) for e in es):
            break
        time.sleep(0.2)
    rec = {"log": [], "statement": None, "params": {}, "auto": None}
    for i, e in enumerate(es):
        if is_statement(e):
            rec["log"] = [e]
            rec["statement"] = EXECUTE.search(e).group(2)
            if i + 1 < len(es) and "DETAIL:  Parameters:" in es[i + 1]:
                rec["log"].append(es[i + 1])
                rec["params"] = dict(PARAM.findall(es[i + 1]))
        if is_plan(e):
            p = parse_plan(e)
            rec["auto"] = {"nodes": p["nodes"], "conds": p["conds"]}
    if rec["statement"] is not None:
        rec["psql"] = re.sub(r"\$(\d+)\b", lambda m: rec["params"].get(m.group(1), m.group(0)), rec["statement"])
    say(f"--- server log for app={app}\n" + "\n".join(es))
    return rec


def app_case(app, sql, value, client=False, prepare=None):
    factory = ClientCursor if client else psycopg.Cursor
    with psycopg.connect(DSN, application_name=app, autocommit=True, cursor_factory=factory) as conn:
        conn.execute(sql, (value,), prepare=prepare).fetchall()
        types = None
        if prepare:
            types = [r[0] for r in conn.execute(
                "SELECT parameter_types::text FROM pg_prepared_statements WHERE name LIKE '\\_pg3\\_%' ORDER BY name",
                prepare=False).fetchall()]
    rec = read_app_log(app)
    rec.update(sql=sql, sent=repr(value), cursor="ClientCursor" if client else "default cursor", parameter_types=types)
    data["app"][app] = rec
    if rec["statement"] is None:
        data["invalid"].append(f"no statement for app={app} in the server log")
    elif not prepare:
        explain(app + " psql", rec["psql"])
    return rec


def setup():
    row = psql(
        "SELECT split_part(current_setting('server_version'), ' ', 1), "
        "CASE datlocprovider WHEN 'c' THEN 'libc' WHEN 'i' THEN 'icu' WHEN 'b' THEN 'builtin' END, datcollate, "
        "(SELECT count(*) FROM members) FROM pg_database WHERE datname = 'lab';").strip().split("|")
    data["setup"] = {"version": row[0], "provider": row[1], "collate": row[2], "rows": row[3], "psycopg": psycopg.__version__}


def baseline():
    data["base"] = []
    for sql in BASE:
        r = explain("base " + sql, sql)
        data["base"].append(sql)
        if not any("Index" in n for n in r["nodes"]):
            data["invalid"].append(f"base query does not use an index: {sql}")


def part_explain():
    off = "SET enable_seqscan = off;"
    data["p1"] = []
    for sql in [ID_NUMERIC, LIKE_LITERAL, LOWER_LITERAL]:
        explain("p1 on " + sql, sql)
        explain("p1 off " + sql, sql, pre=off)
        data["p1"].append(sql)
    data["p3"] = "SELECT * FROM members WHERE code = 'C0000042'::text"
    explain("p3", data["p3"], pre=off)


def part_causes():
    explain("p4", "EXECUTE q(42)", pre="PREPARE q(bigint) AS SELECT * FROM members WHERE id = $1;")

    psql("DROP INDEX members_code_idx;")
    data["p5"] = []
    for cols in ["(code)", "(code text_pattern_ops)", "(code COLLATE \"C\")"]:
        ddl = f"CREATE INDEX members_code_like_idx ON members {cols}"
        psql(ddl + ";")
        explain("p5 " + ddl, LIKE_LITERAL)
        psql("DROP INDEX members_code_like_idx;")
        data["p5"].append(ddl)

    ddl = "CREATE INDEX ON members (lower(email))"
    psql(ddl + ";")
    explain("p6", LOWER_LITERAL)
    data["p6"] = ddl


def part_log():
    app_case("client-float", ID_SQL, 42.0, client=True)
    app_case("decimal-42", ID_SQL, Decimal("42"))
    app_case("decimal-42-prepare", ID_SQL, Decimal("42"), prepare=True)
    explain("p8 prepare", "EXECUTE q('42')", pre="PREPARE q(numeric) AS SELECT * FROM members WHERE id = $1;")
    app_case("decimal-42.00", ID_SQL, Decimal("42.00"))


def main():
    t0 = time.monotonic()
    setup()
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
