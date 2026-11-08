import json
import pathlib
import sys

results = pathlib.Path(sys.argv[1])
ENVOY = {
    "b3": "B3 (zipkin tracer, default)",
    "both": "B3 and W3C (zipkin tracer, USE_B3_WITH_W3C_PROPAGATION)",
    "w3c": "W3C (OpenTelemetry tracer)",
    "none": "no tracer",
}
SECTION_3 = ["before", "b-w3c"]
SECTION_5 = [
    "apps-both",
    "gateway-both",
    "gateway-w3c",
    "gateway-no-tracer",
    "manual-forward",
    "a-w3c-only",
    "left-behind",
    "after",
]
SECTION_5_4 = ["before", "apps-both", "after"]


def load(name):
    path = results / name / "analysis.json"
    return json.loads(path.read_text()) if path.exists() else None


def table(header, rows):
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for row in rows:
        print("| " + " | ".join(str(c) for c in row) + " |")
    print()


def trace_list(a):
    return " / ".join("(" + ", ".join(t["services"]) + ")" for t in a["traces"])


def root_list(a):
    return ", ".join(f"{s}: {n}" for s, n in a["root_spans"].items() if n) or "none"


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
    print(
        f"{ratio['requests']} `curl` requests one at a time through Envoy, only b switched to W3C, "
        f"`OTEL_TRACES_SAMPLER=parentbased_traceidratio` with ratio {ratio['condition']['sampler_ratio']}. "
        "Each row counts the requests that left spans from those services."
    )
    print()
    table(
        ["Services with spans for the request", "Requests"],
        sorted(ratio["services_per_request"].items(), key=lambda kv: -kv[1]),
    )

present = [n for n in SECTION_5 if data.get(n)]
if present:
    print("## Section 5 (job `section-5`)")
    print()
    print("One `curl` without trace headers through Envoy for each stage, all requests sampled.")
    print()
    table(
        ["Stage", "Envoy", "a", "b", "c", "Traces for one request"],
        [
            [
                c["label"],
                ENVOY[c["envoy"]],
                app(c["a"]),
                app(c["b"]),
                app(c["c"]),
                data[n]["trace_count"],
            ]
            for n in present
            for c in [data[n]["condition"]]
        ],
    )

rows = []
for n in ["apps-both", "left-behind", "gateway-no-tracer"]:
    a = data.get(n)
    if not a:
        continue
    for target, s in a["sent"].items():
        to = "a through Envoy without a tracer" if target == "gateway" else f"{target} directly"
        service = "a" if target == "gateway" else target
        rows.append(
            [to, a["condition"]["label"], "yes" if service in s["services"] else "no"]
        )
if rows:
    print("## Sections 5-2 and 5-5 checks (job `section-5`)")
    print()
    print(
        "One request carrying only a fixed `traceparent`, then a look in Jaeger for the service's span "
        "under that trace ID."
    )
    print()
    table(["Sent to", "Condition", "Service recorded under the sent trace ID"], rows)

rows = []
for n in SECTION_5_4:
    a = data.get(n)
    if a:
        rows.append(
            [f"`{a['condition']['c']}`", " ".join(f"`{c['comment']}`" for c in a["sql_comments"]) or "none"]
        )
if rows:
    print("## Section 5-4 (jobs `section-3`, `section-5`)")
    print()
    print("The comment on c's query in the PostgreSQL `log_statement=all` log, for one request.")
    print()
    table(["c's `OTEL_PROPAGATORS`", "SQL comment"], rows)

for m in invalid:
    print(f"RUN INVALID: {m}")
sys.exit(1 if invalid else 0)
