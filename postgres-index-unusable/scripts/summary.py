import json
import sys
from pathlib import Path

SLUG = "postgres-index-unusable"
JOBS = {"explain": "2 (explain)", "causes": "3 (causes)", "log": "4 (log)"}
LOG_CASES = ["client-float", "decimal-42", "decimal-42.00"]

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


def table(head, rows):
    w("| " + " | ".join(head) + " |")
    w("|" + "---|" * len(head))
    for r in rows:
        w("| " + " | ".join(r) + " |")
    w()


def node(c):
    return "<br>".join(c["nodes"]) if c["nodes"] else "-"


def statements(c):
    return cell(([c["pre"]] if c["pre"] else []) + [c["sql"]])


w(f"# {SLUG}")
w()
for m in invalid:
    w(m)
if invalid:
    w()
if parts:
    s = next(iter(parts.values()))["setup"]
    w(f"PostgreSQL {s['version']}, database lab {s['provider']} {s['collate']}, psycopg {s['psycopg']}, members {s['rows']} rows")
    w()
w("Scan line: the line of each scan node in the plan, up to `(cost=`. Condition line: each `Index Cond:` and `Filter:` line as printed.")
w()

d = parts.get("explain")
if d and not d["invalid"]:
    j = JOBS["explain"]
    c = d["cases"]
    w("## 2. Reading the EXPLAIN output")
    w()
    w(f"Job `{j}`: `EXPLAIN (ANALYZE)` with the default `enable_seqscan = on` and after `SET enable_seqscan = off`")
    w()
    table(["Statement", "`on`: scan line", "`off`: scan line", "`off`: condition line"],
          [[code(q), node(c["p1 on " + q]), node(c["p1 off " + q]), cell(c["p1 off " + q]["conds"])] for q in d["p1"]])
    w(f"Job `{j}`: `EXPLAIN (ANALYZE)` after `SET enable_seqscan = off`")
    w()
    table(["Statement", "Scan line", "Condition line"], [[code(d["p3"]), node(c["p3"]), cell(c["p3"]["conds"])]])

d = parts.get("causes")
if d and not d["invalid"]:
    j = JOBS["causes"]
    c = d["cases"]
    w("## 3. How each cause happens")
    w()
    w(f"Job `{j}`: in one psql session, with `EXPLAIN (ANALYZE)` before `EXECUTE`")
    w()
    table(["Statements", "Scan line"], [[statements(c["p4"]), node(c["p4"])]])
    w(f"Job `{j}`: `members_code_idx` dropped; each index created, `EXPLAIN (ANALYZE) {c['p5 ' + d['p5'][0]]['sql']}`, the index dropped")
    w()
    table(["Index", "Scan line"], [[code(ddl), node(c["p5 " + ddl])] for ddl in d["p5"]])
    w(f"Job `{j}`: `EXPLAIN (ANALYZE) {c['p6']['sql']}` after the index is created")
    w()
    table(["Index", "Scan line"], [[code(d["p6"]), node(c["p6"])]])

d = parts.get("log")
if d and not d["invalid"]:
    j = JOBS["log"]
    c = d["cases"]
    a = d["app"]
    w("## 4. Checking with the logged statement")
    w()
    w(f"Job `{j}`: each statement sent from psycopg on a new connection; the statement found in the server log, "
      "with `$1` replaced by the logged value and its quotes, run in psql with `EXPLAIN (ANALYZE)`")
    w()
    rows = []
    for k in LOG_CASES:
        r = c[k + " psql"]
        rows.append([f"{a[k]['cursor']}, {code(a[k]['sql'])}, {code(a[k]['sent'])}", code(a[k]["psql"]), node(r),
                     code(r["error"]) if r["error"] else cell(r["conds"])])
    table(["Sent from psycopg", "Run in psql", "Scan line", "Condition line or error"], rows)
    w(f"Job `{j}`: the server log lines of the statements above, in the same order")
    w()
    w("```")
    w("\n".join(l for k in LOG_CASES for l in a[k]["log"]))
    w("```")
    w()
    u, p = a["decimal-42"], a["decimal-42-prepare"]
    w(f"Job `{j}`: the plan auto_explain logged for the statement sent with `{u['sent']}` above, "
      "and `pg_prepared_statements.parameter_types` for the same value sent with `prepare=True` on a new connection")
    w()
    table(["Sent", "auto_explain condition line", "`parameter_types` with `prepare=True`"],
          [[code(u["sent"]), cell(u["auto"]["conds"]) if u["auto"] else "-", cell(p["parameter_types"])]])
    w(f"Job `{j}`: in one psql session, with `EXPLAIN (ANALYZE)` before `EXECUTE`")
    w()
    table(["Statements", "Condition line"], [[statements(c["p8 prepare"]), cell(c["p8 prepare"]["conds"])]])

w("## Numbers not used")
w()
if parts:
    keys = list(parts)
    first = parts[keys[0]]
    w("Base queries: each job stops with RUN INVALID unless every one of them uses an index")
    w()
    table(["Statement"] + [f"`{JOBS[k]}`: scan line" for k in keys],
          [[code(q)] + [node(parts[k]["cases"]["base " + q]) for k in keys] for q in first.get("base", [])])

print("\n".join(lines).rstrip("\n"))
sys.exit(1 if invalid else 0)
