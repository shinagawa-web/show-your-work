import json
import pathlib
import sys

results = pathlib.Path(sys.argv[1])
ENVOY = {
    "b3": "B3 (zipkin tracer, default)",
    "b3-sampled-10": "B3 (zipkin tracer, default, random_sampling 10%)",
    "both": "B3 and W3C (zipkin tracer, USE_B3_WITH_W3C_PROPAGATION)",
    "w3c": "W3C (OpenTelemetry tracer)",
    "none": "no tracer",
}
SECTION_3 = ["before", "b-w3c"]
SECTION_4 = [
    "apps-both",
    "gateway-both",
    "gateway-w3c",
    "gateway-w3c-first",
    "gateway-no-tracer",
    "manual-forward",
    "a-w3c-only",
    "left-behind-hidden",
    "left-behind",
    "after",
]
SECTION_4_4 = ["before", "apps-both", "after"]


def load(name):
    path = results / name / "analysis.json"
    return json.loads(path.read_text()) if path.exists() else None


def table(header, rows, right=()):
    print("| " + " | ".join(header) + " |")
    print("|" + "".join("---:|" if i in right else "---|" for i in range(len(header))))
    for row in rows:
        print("| " + " | ".join(str(c) for c in row) + " |")
    print()


def trace_list(a):
    return " / ".join("(" + ", ".join(t["services"]) + ")" for t in a["traces"])


def root_list(a):
    return ", ".join(f"{s}: {n}" for s, n in a["root_spans"].items() if n) or "none"


def roots_outside_entry(a):
    entry = "a" if a["condition"]["envoy"] == "none" else "envoy"
    return ", ".join(f"{s}: {n}" for s, n in a["root_spans"].items() if n and s != entry) or "none"


def app(value):
    return "no SDK, forwards x-b3-* by hand" if value == "manual" else f"`{value}`"


data = {p.name: load(p.name) for p in sorted(results.iterdir()) if (p / "settings.env").exists()}
invalid = []
for name, a in data.items():
    if a is None:
        invalid.append(f"{name}: no analysis")
    else:
        invalid += [f"{name}: {m}" for m in a["invalid"]]

print("# b3-to-w3c-migration")
print()

both = data.get("apps-both")
if both:
    print("## Section 2 (job `section-4`)")
    print()
    print(
        f"The trace headers c received from b for one request at the stage {both['condition']['label']}, "
        "names and values as c's handler read them. Envoy's zipkin tracer started the trace with its default "
        "64-bit trace ID, so the left 16 hex digits of the trace ID are 0."
    )
    print()
    table(["Header", "Value"], [[f"`{h['name']}`", f"`{h['value']}`"] for h in both["c_received_headers"]])
    if not both["c_received_headers"]:
        invalid.append("apps-both: c printed no trace headers")

if data.get("before") and data.get("b-w3c"):
    print("## Section 3 (job `section-3`)")
    print()
    print(
        "One `curl` without trace headers through Envoy (Envoy writes B3, every request sampled), "
        "then the spans of envoy, a, b and c read from Jaeger."
    )
    print()
    table(
        ["Condition", "Traces for one request", "Services in each trace", "Root spans by service"],
        [
            [data[n]["condition"]["label"], data[n]["trace_count"], trace_list(data[n]), root_list(data[n])]
            for n in SECTION_3
        ],
    )

ratio = data.get("b-w3c-ratio")
if ratio:
    shapes = {}
    for t in ratio["traces"]:
        key = tuple(t["services"])
        shapes[key] = shapes.get(key, 0) + 1
    labels = {
        ("envoy", "a"): "envoy → a",
        ("b",): "b alone (new trace started where the trace broke)",
        ("c",): "c alone (new trace started where the trace broke)",
    }
    invalid += [f"b-w3c-ratio: unexpected trace with {', '.join(k)}" for k in shapes if k not in labels]
    print(
        f"{ratio['requests']} `curl` requests one at a time through Envoy, only b switched to W3C, "
        "with Envoy recording 10% of requests (`random_sampling` 10) and the apps 10% "
        f"(`OTEL_TRACES_SAMPLER=parentbased_traceidratio`, ratio {ratio['condition']['sampler_ratio']}). "
        "Each request splits into up to three traces; each row counts the traces of one kind found in Jaeger, "
        "which is the number of requests that recorded that trace."
    )
    print()
    table(
        ["Trace", f"Requests that recorded it (of {ratio['requests']})"],
        [[label, shapes.get(key, 0)] for key, label in labels.items()],
        right=(1,),
    )

present = [n for n in SECTION_4 if data.get(n)]
if present:
    print("## Section 4 (job `section-4`)")
    print()
    print(
        "One `curl` without trace headers through Envoy for each stage, all requests sampled. "
        "The last column counts spans with no parent from every service except the entry, "
        "which is envoy when Envoy starts the trace and a when Envoy has no tracer."
    )
    print()
    table(
        ["Stage", "Envoy", "a", "b", "c", "Root spans outside the entry"],
        [
            [
                c["label"],
                ENVOY[c["envoy"]],
                app(c["a"]),
                app(c["b"]),
                app(c["c"]),
                roots_outside_entry(data[n]),
            ]
            for n in present
            for c in [data[n]["condition"]]
        ],
    )

def recorded(a, target):
    return "yes" if target in a["sent"][target]["services"] else "no"


rows = []
for n in ["apps-both", "left-behind-hidden"]:
    a = data.get(n)
    if a:
        rows.append([a["condition"]["label"]] + [recorded(a, s) for s in ["a", "b", "c"]])
no_tracer = data.get("gateway-no-tracer")
if rows or no_tracer:
    print("## Sections 4-2 and 4-5 checks (job `section-4`)")
    print()
if rows:
    print(
        "One request carrying only a fixed `traceparent` sent directly to each of a, b and c. "
        "Each cell says whether that service recorded a span under the trace ID it was sent."
    )
    print()
    table(["Condition", "a", "b", "c"], rows)
if no_tracer:
    sent = no_tracer["sent"]["gateway"]
    verb = "was" if "a" in sent["services"] else "was not"
    print(
        "A request carrying only a fixed `traceparent` through Envoy without a tracer: "
        f"the trace ID {verb} recorded on a's span."
    )
    print()

rows = []
for n in SECTION_4_4:
    a = data.get(n)
    if a:
        rows.append(
            [f"`{a['condition']['c']}`", " ".join(f"`{c['comment']}`" for c in a["sql_comments"]) or "none"]
        )
if rows:
    print("## Section 4-4 (jobs `section-3`, `section-4`)")
    print()
    db_ok = [
        n
        for n, a in data.items()
        if a and a["db_spans"] and a["db_spans"] == a["db_spans_with_c_server_parent"]
        and not a["db_traces_without_c_server_span"]
    ]
    db_bad = sorted(n for n, a in data.items() if a and n not in db_ok)
    if db_bad:
        db_sentence = "The DB spans (names starting with `sql.`) were not all children of c's server span in: " + ", ".join(db_bad) + "."
    else:
        db_sentence = "In every condition, the DB spans (names starting with `sql.`) were children of c's server span, in the same trace."
    print(
        "The comment on c's query in the PostgreSQL `log_statement=all` log, for one request, "
        "at the stages before the migration, 4-1 apps read and write both, and after the migration (top to bottom). "
        "In the first two, Envoy's zipkin tracer starts the trace with its default 64-bit trace ID, "
        "so the left 16 hex digits are 0; after the migration, Envoy's OpenTelemetry tracer starts it with a 128-bit ID. "
        + db_sentence
    )
    print()
    table(["c's `OTEL_PROPAGATORS`", "SQL comment"], rows)

for m in invalid:
    print(f"RUN INVALID: {m}")
sys.exit(1 if invalid else 0)
