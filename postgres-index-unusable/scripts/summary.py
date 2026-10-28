import json
import re
import sys
from pathlib import Path

SLUG = "postgres-index-unusable"
JOBS = {"log": "2 (log)", "reproduced": "3 (reproduced)", "not-reproduced": "4 (not-reproduced)", "types": "5 (types)"}

root = Path(sys.argv[1])
wanted = sys.argv[2:] or list(JOBS)
parts = {}
invalid = []
for p in JOBS:
    f = root / p / "data.json"
    if f.exists():
        parts[p] = json.loads(f.read_text())
        invalid += [f"RUN INVALID: `{JOBS[p]}`: {m}" for m in parts[p]["invalid"]]
    elif (root / p / "invalid.txt").exists():
        invalid += [f"`{JOBS[p]}`: {l}" for l in (root / p / "invalid.txt").read_text().splitlines()]
    elif p in wanted:
        invalid.append(f"RUN INVALID: `{JOBS[p]}`: no results/{p}/data.json")

lines = []


def w(s=""):
    lines.append(s)


def code(s):
    return "`" + s.replace("|", "\\|") + "`" if s else "-"


def cell(items):
    return "<br>".join(code(i) for i in items) if items else "-"


def block(text):
    w("```")
    w(text.rstrip("\n"))
    w("```")
    w()


def table(head, rows):
    w("| " + " | ".join(head) + " |")
    w("|" + "---|" * len(head))
    for r in rows:
        w("| " + " | ".join(r) + " |")
    w()


def result(c):
    return c["exec_time"] if c["error"] is None else code(c["error"])


def node(c):
    return " / ".join(c["nodes"]) if c["nodes"] else "-"


def estimated(c):
    m = re.search(r"\(cost=[^)]* rows=(\d+)", c["output"])
    return m.group(1) if m else "-"


def plan_row(c):
    return [code(c["sql"]), node(c), cell(c["conds"]), result(c)]


def rows_setting():
    for p in parts:
        f = root / p / "settings.txt"
        if f.exists():
            for l in f.read_text().splitlines():
                k, _, v = l.partition("\t")
                if k == "rows":
                    return v
    return "?"


w(f"# {SLUG}")
w()
for m in invalid:
    w(m)
if invalid:
    w()
if parts:
    first = next(iter(parts.values()))
    django = parts.get("reproduced", {}).get("django", {}).get("django")
    env = f"PostgreSQL {first['server_version']}, database `lab` libc en_US.utf8, psycopg {first['psycopg']}"
    if django:
        env += f", Django {django}"
    w(env + f", `members` {rows_setting()} rows")
    w()

d = parts.get("log")
if d and not d["invalid"]:
    j = JOBS["log"]
    keys = ["base", "p1-decimal", "p1-float", "p2", "p3-like", "p3-lower", "p3-status", "p4"]
    w("## 2. Running the logged statement with the logged values")
    w()
    w(f"Job `{j}`: each case on a new connection with its own `application_name`; "
      "the logged statement is run in psql with `EXPLAIN (ANALYZE)`, `$1` replaced by the logged value with its quotes")
    w()
    rows = []
    for key in keys:
        a = d["app"][key]
        c = d["cases"][key + "-psql"]
        rows.append([code(f"app={key}"), f"{a['cursor']}, {code(a['sql'])}, {code(a['sent'])}", code(a["psql"]),
                     node(c), cell(c["conds"]), result(c)])
    table(["Case, `app=` in the log line", "Sent", "Run in psql", "Node", "Condition line", "Execution Time or error"], rows)
    log = []
    for key in keys:
        a = d["app"][key]
        log.append(a["execute"])
        if a["detail"]:
            log.append(a["detail"])
    block("\n".join(log))

    p6 = d["p6"]
    w(f"Job `{j}`: `pg_dump -t members` of `lab`, restored without `--create` with psql into `staging`, "
      "created with `LOCALE_PROVIDER libc LOCALE 'C' TEMPLATE template0`")
    w()
    block("\n".join(p6["l_lines"]))
    w("`staging`, right after the restore:")
    w()
    block(p6["stat_before"])
    table(["`staging`", "Statement", "Node", "Condition line", "Execution Time"],
          [[when] + plan_row(d["cases"][k]) for when, k in [("before `ANALYZE`", "p6-before"), ("after `ANALYZE members`", "p6-after")]])

d = parts.get("reproduced")
if d and not d["invalid"]:
    j = JOBS["reproduced"]
    w("## 3. When it reproduces")
    w()
    w(f"Job `{j}`")
    w()
    block(d["text"]["d"])
    w(f"Job `{j}`: `SET enable_seqscan = off` before each statement")
    w()
    table(["Statement", "Node", "Condition line", "`Disabled: true`"],
          [plan_row(d["cases"][k])[:3] + ["yes" if d["cases"][k]["disabled"] else "no"] for k in ["p7-like", "p7-lower", "p7-status", "p7-p4"]])
    w(f"Job `{j}`: the `varchar` column compared with a `text` value")
    w()
    table(["Statement", "Node", "Condition line"], [plan_row(d["cases"]["p8"])[:3]])
    w(f"Job `{j}`: `members_code_idx` dropped; each index created, `{d['cases']['p9 default']['sql']}` explained, the index dropped")
    w()
    table(["Index", "Node", "Condition lines", "Execution Time"],
          [[code(ddl)] + plan_row(d["cases"]["p9 " + label])[1:] for label, ddl in d["p9"]])
    if "django" in d:
        w(f"Job `{j}`: `CREATE INDEX` statements collected by `connection.schema_editor(collect_sql=True)` for a model with "
          "`CharField(db_index=True)`, `CharField(unique=True)` and a `CharField` in `Meta.indexes`")
        w()
        block("\n".join(s for s in d["django"]["sql"] if s.startswith("CREATE INDEX")))

d = parts.get("not-reproduced")
if d and not d["invalid"]:
    j = JOBS["not-reproduced"]
    w("## 4. When it does not reproduce")
    w()
    w(f"Job `{j}`: one connection with the default cursor, `SELECT * FROM members WHERE status = %s` "
      "10 times with `'active'`, then with `'locked'`; `pg_prepared_statements` read after each execution")
    w()
    rows = []
    for r in d["p12"]:
        ps = r["prepared"][0] if r["prepared"] else None
        rows.append([str(r["n"]), code(r["value"]), code(r["name"]), str(ps[2]) if ps else "-", str(ps[3]) if ps else "-",
                     " / ".join(r["auto"]["nodes"]) if r["auto"] else "-", cell(r["auto"]["conds"]) if r["auto"] else "-"])
    table(["Execution", "Value", "Name after `execute`", "`generic_plans`", "`custom_plans`", "auto_explain node", "auto_explain condition line"], rows)
    w(f"Job `{j}`: the logged values run in psql")
    w()
    table(["Statement", "Node", "Condition line", "Execution Time"], [plan_row(d["cases"][k]) for k in ["p12-psql active", "p12-psql locked"]])
    w(f"Job `{j}`: the logged value `'42'` of a `Decimal` or `float` parameter, through `PREPARE` with the type psycopg sent")
    w()
    table(["Statement", "Node", "Condition line", "Execution Time"],
          [[cell([d["cases"][k]["pre"], "EXPLAIN (ANALYZE) " + d["cases"][k]["sql"]])] + plan_row(d["cases"][k])[1:] for k in ["p11-numeric", "p11-double"]])
    w(f"Job `{j}`: `PREPARE q(varchar) AS SELECT * FROM members WHERE status = $1;` then `EXPLAIN (ANALYZE) EXECUTE q('locked')`")
    w()
    table(["`plan_cache_mode`", "Node", "Estimated rows", "Condition line", "Execution Time"],
          [[mode, node(d["cases"][k]), estimated(d["cases"][k]), cell(d["cases"][k]["conds"]), result(d["cases"][k])]
           for mode, k in [("`auto` (default)", "p13-default"), ("`force_generic_plan`", "p13-generic")]])

d = parts.get("types")
if d and not d["invalid"]:
    j = JOBS["types"]
    w("## 5. How the type mismatch happens")
    w()
    w(f"Job `{j}`: `PREPARE q(<parameter type>) AS SELECT * FROM members WHERE <column> = $1;` then `EXPLAIN (ANALYZE) EXECUTE q(<value>)`")
    w()
    rows = []
    for column, ptype, pre, ex, key in d["p14"]:
        c = d["cases"][key]
        rows.append([code(column), code(ptype), code(pre), code(ex.replace("EXPLAIN (ANALYZE) ", "")), node(c), cell(c["conds"]), result(c)])
    table(["Column", "Parameter type", "Prepared", "Executed", "Node", "Condition line", "Execution Time or error"], rows)
    w(f"Job `{j}`: psycopg default cursor, `{d['app']['types-int']['sql']}` with `prepare=True`, each value on a new connection")
    w()
    rows = []
    for key in ["types-int", "types-decimal", "types-float"]:
        a = d["app"][key]
        ps = a["prepared"][0] if a["prepared"] else ["-", "-"]
        rows.append([code(a["sent"]), code(ps[1]), cell(a["auto"]["conds"]) if a["auto"] else "-"])
    table(["Sent", "`pg_prepared_statements.parameter_types`", "auto_explain condition line"], rows)

w("## Numbers not used")
w()
if parts:
    first_key, first = next(iter(parts.items()))
    w(f"Setup, job `{JOBS[first_key]}`: the server runs with the logging settings and the default locale, and `status` has the intended distribution")
    w()
    block(first["text"]["settings"] + first["text"]["datcollate"] + first["text"]["counts"])
    keys = list(parts)
    w("Base queries: each job stops with RUN INVALID unless every one of them uses an index")
    w()
    table(["Statement"] + [f"`{JOBS[k]}`" for k in keys],
          [[code(sql)] + [node(parts[k]["cases"]["base " + label]) for k in keys] for label, sql, _ in first.get("base", [])])
d = parts.get("reproduced")
if d and "pg_collation" in d["text"]:
    w(f"`pg_collation`, job `{JOBS['reproduced']}`: the collations the indexes of section 3 use exist, with their provider")
    w()
    block(d["text"]["pg_collation"])
if d and "django" in d:
    w(f"Django model, job `{JOBS['reproduced']}`: the model the `CREATE INDEX` statements of section 3 were collected for")
    w()
    w("```python")
    w(d["django"]["model"].rstrip("\n"))
    w("```")
    w()

print("\n".join(lines).rstrip("\n"))
sys.exit(1 if invalid else 0)
