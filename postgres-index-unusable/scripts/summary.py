#!/usr/bin/env python3
import json
import os
import sys

results = sys.argv[1]
SECTIONS = {"explain": "2", "causes": "3"}
names = list(SECTIONS)
expected = sys.argv[2:] or names
unknown = [p for p in expected if p not in names]
if unknown:
    sys.exit(f"unknown part: {' '.join(unknown)} (one of: {' '.join(names)})")

invalid, runs = [], {}
for part in names:
    if part not in expected:
        continue
    job = f"{SECTIONS[part]} ({part})"
    path = os.path.join(results, part, "invalid.txt")
    if os.path.exists(path):
        invalid += [l.removeprefix("RUN INVALID: ") for l in open(path).read().split("\n") if l.strip()]
    path = os.path.join(results, part, "data.json")
    if not os.path.exists(path):
        invalid.append(f"job `{job}`: no {path}")
        continue
    d = json.load(open(path))
    settings = os.path.join(results, part, "settings.txt")
    if os.path.exists(settings):
        d["settings"] = dict(l.split("\t", 1) for l in open(settings).read().split("\n") if "\t" in l)
    runs[part] = d


def code(text, lang="text"):
    return [f"```{lang}", text, "```", ""]


def nodes(plan):
    out = []
    for l in plan.split("\n"):
        if "(cost=" in l or "(actual" in l:
            out.append(l.strip().removeprefix("->").strip().split("  (cost=")[0].split(" (actual")[0])
    return " → ".join(out)


def conds(plan):
    return [l.strip() for l in plan.split("\n") if l.strip().startswith(("Index Cond:", "Filter:"))]


def cell(text):
    return f"`{text}`" if text else "-"


def cond_cell(plan):
    if plan.startswith("ERROR"):
        return cell(plan.split("\n")[0])
    return "<br>".join(cell(c) for c in conds(plan)) or "-"


def disabled(plan):
    return "yes" if "Disabled: true" in plan else "no"


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return out + ["| " + " | ".join(str(v) for v in r) + " |" for r in rows] + [""]


def psql_rows(text):
    lines = text.split("\n")
    sep = next(i for i, l in enumerate(lines) if set(l.strip()) <= set("-+") and l.strip())
    head = [c.strip() for c in lines[sep - 1].split("|")]
    rows = [[c.strip() for c in l.split("|")] for l in lines[sep + 1:]
            if "|" in l and not l.startswith("(")]
    return head, rows


PLAN_HEAD = ["", "Condition", "enable_seqscan", "Node", "Disabled: true", "Condition line"]


def plan_row(label, condition, d, off):
    plan = d["plan_off"] if off else d["plan"]
    return [label, condition, "off" if off else "on", nodes(plan), disabled(plan), cond_cell(plan)]


def environment():
    first = next(iter(runs.values()))
    s = first["setup"]
    parts = [" ".join(s["version"].split()[:2])]
    rows = first.get("settings", {}).get("rows")
    if rows:
        parts.append(f"{rows} rows in members")
    parts.append(f"max_parallel_workers_per_gather {s['max_parallel_workers_per_gather']}")
    dbs = runs["causes"]["databases"] if "causes" in runs else None
    if dbs:
        head, rows = psql_rows(dbs)
        lab = next((r for r in rows if r[0] == "lab"), None)
        if lab:
            parts.append(f"database collation {lab[head.index('Locale Provider')]} {lab[head.index('Collate')]}")
    return ", ".join(parts)


def section2(d):
    out = ["## 2. EXPLAIN output and the cause", "",
           f"Job `{d['job']}`: the type of a parameter is set with `PREPARE q(<type>)` and the value passed with "
           "`EXPLAIN EXECUTE q(...)`", "", "`\\d members`:", ""] + code(d["describe"])
    rows = [
        plan_row("P1", "`id` (integer) = numeric parameter", d["p1_numeric"], True),
        plan_row("P7", "`code LIKE 'C000004%'`, default collation index", d["p7_default"], True),
        plan_row("P8", "`lower(email) = 'user42@example.com'`", d["p8"], True),
        plan_row("P6", "`code` (varchar) = text parameter", d["p6"], False),
        plan_row("P10", "P1 with `SELECT id`", d["p10_p1"], False),
        plan_row("P10", "P7 with `SELECT code`", d["p10_p7"], True),
        plan_row("P10", "P8 with `SELECT email`", d["p10_p8"], True),
    ]
    out += table(PLAN_HEAD, rows)
    counts = dict(r[:2] for r in psql_rows(d["status_counts"])[1])
    out += ["### P9: a value most rows have", "",
            f"Job `{d['job']}`: `status = 'active'`, {counts.get('t', '-')} of "
            f"{int(counts.get('t', 0)) + int(counts.get('f', 0))} rows have `active`", "",
            "`enable_seqscan` on:", ""] + code(d["p9"]["plan"])
    out += ["`enable_seqscan` off:", ""] + code(d["p9"]["plan_off"])
    out += ["### `EXPLAIN (GENERIC_PLAN)` of a statement with `$1`", "",
            f"Job `{d['job']}`: a `text_pattern_ops` index on `code` exists for the LIKE", ""]
    out += table(["Statement", "Node", "Condition line"],
                 [[cell(g["sql"].rstrip(";")), nodes(g["plan"]), cond_cell(g["plan"])]
                  for g in d["generic_plan"][:2]])
    return out


TYPES = {"p2": ("integer", "bigint"), "p3": ("integer", "varchar"), "p4": ("char(10)", "varchar"),
         "p5": ("char(10)", "text")}


def p14_section(d):
    p14 = d["p14"]
    out = ["### P14: psycopg 3 and the server log", "",
           f"Job `{d['job']}`: psycopg {p14['psycopg']} (libpq {p14['libpq']}) runs "
           "`cur.execute(\"SELECT * FROM members WHERE id = %s\", (value,))` with `Decimal(\"42\")` and `42.0`, "
           "each on its own connection", ""]
    rows = []
    for r in p14["values"]:
        log = r["log"]
        i = next((n for n, l in enumerate(log) if "execute <unnamed>:" in l), None)
        if r["label"] == "decimal":
            out += [f"Server log for `{r['value']}`, the `execute` line and the line after it:", ""]
            out += code("\n".join(log[i:i + 2]) if i is not None else "(not found)")
            if "pasted_plan" in r:
                rows.append([cell(r["value"]), f"logged statement with the logged value, `{r['pasted_sql'].rstrip(';')}`",
                             nodes(r["pasted_plan"]), cond_cell(r["pasted_plan"])])
        else:
            out += [f"Server log for `{r['value']}`, the line after `execute`:", ""]
            out += code(log[i + 1] if i is not None and i + 1 < len(log) else "(not found)")
        if "prepared_plan" in r:
            how = "; ".join(l.rstrip(";") for l in r["prepared_sql"].split("\n"))
            rows.append([cell(r["value"]), f"type psycopg sent, `{how}`", nodes(r["prepared_plan"]),
                         cond_cell(r["prepared_plan"])])
    out += ["The logged value, explained in psql:", ""]
    out += table(["Value", "How it was run", "Node", "Condition line"], rows)
    return out


def p13_section(d):
    p13 = d["p13"]
    out = ["### P13: pg_dump restored without `--create` into a database whose default collation is `C`", "",
           f"Job `{d['job']}`: `staging` created with `LOCALE_PROVIDER libc LOCALE 'C'`, "
           "`pg_dump -t members lab` restored into it with psql, then `ANALYZE`, and "
           "`EXPLAIN SELECT * FROM members WHERE code LIKE 'C000004%'` on both", ""]
    lines = p13["databases"].split("\n")
    out += ["`\\l`, the header and the rows of `lab` and `staging`:", ""]
    out += code("\n".join(lines[:3] + [l for l in lines[3:] if l.startswith((" lab ", " staging "))]))
    out += table(["Database", "Node", "Condition line"],
                 [["`lab`", nodes(p13["plan_lab"]), cond_cell(p13["plan_lab"])],
                  ["`staging`", nodes(p13["plan_staging"]), cond_cell(p13["plan_staging"])]])
    return out


def section3(d):
    out = ["## 3. How each cause arises", "", "### P2 to P5: column type and parameter type", "",
           f"Job `{d['job']}`: `PREPARE q(<parameter type>) AS SELECT * FROM members WHERE <column> = $1` and "
           "`EXPLAIN EXECUTE q(...)`", ""]
    rows = []
    for key, (col, param) in TYPES.items():
        plan = d[key]["plan"]
        rows.append([f"P{key[1]}", col, param, "-" if plan.startswith("ERROR") else nodes(plan), cond_cell(plan)])
    out += table(["", "Column type", "Parameter type", "Node", "Condition"], rows)
    out += p14_section(d)
    out += ["### P7: `LIKE 'C000004%'` with one index on `code` at a time", "",
            f"Job `{d['job']}`: `members_code_idx` is dropped, and each index below is created, explained with, "
            "and dropped in turn", ""]
    rows = []
    for r in d["p7"]:
        if "error" in r:
            rows.append([cell(r["ddl"]), "index not created", cell(r["error"])])
            continue
        ic = [c for c in conds(r["plan"]) if c.startswith("Index Cond:")]
        rows.append([cell(r["indexdef"]), "yes" if ic else "no", cell(ic[0]) if ic else "-"])
    out += table(["Index definition", "Index Cond", "Index Cond line"], rows)
    out += p13_section(d)
    out += ["### P12: Django, `CREATE INDEX` statements", "",
            f"Job `{d['job']}`: `code` has `db_index=True`, `slug` has `unique=True`, `shop_code` is indexed "
            "through `Meta.indexes`", ""]
    out += code("\n".join(l for l in d["p12"]["ddl"].split("\n") if l.startswith("CREATE INDEX")), "sql")
    return out


def claim(text):
    return [f"Claim checked: {text}", ""]


def unused():
    out = ["## Numbers not used", ""]
    first = next(iter(runs.values()))
    s = first["setup"]
    out += ["### Setup", ""] + claim("the table is large enough for the planner to choose parallel scans, which is "
                                     "why the plans in sections 2 and 3 show `Parallel Seq Scan`")
    out += table(["`members` heap", "min_parallel_table_scan_size"], [[s["heap_size"], s["min_parallel_table_scan_size"]]])
    titles = [b["title"] for b in first["bases"]]
    out += ["### Base queries", ""] + claim("the base queries use their index; the run is invalid otherwise")
    out += table(["Base query"] + [f"`{d['job']}`" for d in runs.values()],
                 [[t] + [nodes(next(b["plan"] for b in d["bases"] if b["title"] == t)) for d in runs.values()]
                  for t in titles])
    rows = []
    if "explain" in runs:
        rows.append(plan_row("P1", "`id` (integer) = double precision parameter", runs["explain"]["p1_double"], True))
    p5 = runs.get("causes", runs.get("explain"))
    if p5:
        rows.append(plan_row("P5", "`fixed_code` (char(10)) = text parameter", p5["p5"], True))
    if rows:
        out += ["### The other type mismatches with `enable_seqscan = off`", ""]
        out += claim("a double precision parameter on the integer column and a text parameter on the char(10) "
                     "column also leave the condition in `Filter:` when the sequential scan is disabled")
        out += table(PLAN_HEAD, rows)
    if "explain" in runs:
        d = runs["explain"]
        g = d["generic_plan"][2]
        out += [f"### The LIKE with a constant beside `GENERIC_PLAN` (job `{d['job']}`)", ""]
        out += claim("with a constant pattern the same `text_pattern_ops` index is used, so the sequential scan "
                     "under `GENERIC_PLAN` comes from the parameter")
        out += table(["Statement", "Node", "Condition line"], [[cell(g["sql"].rstrip(";")), nodes(g["plan"]),
                                                                 cond_cell(g["plan"])]])
    if "causes" in runs:
        d = runs["causes"]
        head, rows = psql_rows(d["collations"])
        out += [f"### Collations of the P7 indexes (job `{d['job']}`)", ""]
        out += claim("`C.utf8` is a libc collation and `pg_c_utf8` is the builtin one")
        out += table(head, rows)
        out += [f"### Django model and `CREATE TABLE` (job `{d['job']}`)", ""]
        out += claim("the model that produced the `CREATE INDEX` statements in section 3")
        out += code(d["p12"]["model"], "python")
        out += code("\n".join(l for l in d["p12"]["ddl"].split("\n") if l.startswith("CREATE TABLE")), "sql")
        p13 = d["p13"]
        create = [l for l in p13["dump_create"].split("\n") if l.startswith("CREATE DATABASE")]
        out += [f"### pg_dump and `CREATE DATABASE` (job `{d['job']}`)", ""]
        out += claim("only a dump taken with `--create` carries the database's locale and provider")
        out += table(["Dump", "`CREATE DATABASE` lines"],
                     [["`pg_dump --create --schema-only -t members lab`", "<br>".join(cell(l) for l in create) or "0"],
                      ["`pg_dump --schema-only -t members lab`", p13["dump_plain_count"]]])
    return out


lines = ["# postgres-index-unusable", ""]
if runs:
    lines += [environment(), ""]
lines += [f"RUN INVALID: {r}\n" for r in invalid]
for part, render in [("explain", section2), ("causes", section3)]:
    if part in runs:
        lines += render(runs[part])
if runs:
    lines += unused()
print("\n".join(lines).rstrip())
sys.exit(1 if invalid or not runs else 0)
