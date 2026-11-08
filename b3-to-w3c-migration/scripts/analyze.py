import json
import pathlib
import re
import sys

SERVICES = ["envoy", "a", "b", "c"]
SPAN_KIND_SERVER = 2
COMMENT = re.compile(r"/\*.*\*/")


def spans_in(path):
    raw = json.loads(path.read_text())
    if raw["status"] != 200:
        return []
    decoder = json.JSONDecoder()
    text, pos, spans = raw["body"], 0, []
    while pos < len(text):
        doc, pos = decoder.raw_decode(text, pos)
        while pos < len(text) and text[pos].isspace():
            pos += 1
        for rs in doc.get("result", {}).get("resourceSpans", []):
            service = next(
                a["value"]["stringValue"] for a in rs["resource"]["attributes"] if a["key"] == "service.name"
            )
            for ss in rs["scopeSpans"]:
                for s in ss["spans"]:
                    spans.append(
                        {
                            "service": service,
                            "trace_id": s["traceId"],
                            "span_id": s["spanId"],
                            "parent": s.get("parentSpanId", ""),
                            "name": s["name"],
                            "kind": s.get("kind"),
                            "start_ns": int(s["startTimeUnixNano"]),
                        }
                    )
    return spans


def services_of(spans):
    present = {s["service"] for s in spans}
    return [s for s in SERVICES if s in present]


def main(out):
    invalid = []
    conditions = {}
    lines = pathlib.Path("conditions.tsv").read_text().splitlines()
    header = lines[0].split("\t")
    for line in lines[1:]:
        row = dict(zip(header, line.split("\t")))
        conditions[row["name"]] = row
    cond = conditions[out.name]

    unique = {}
    for service in SERVICES:
        for span in spans_in(out / "traces" / f"service-{service}.json"):
            unique[span["span_id"]] = span
    spans = list(unique.values())

    requests = []
    for line in (out / "requests.tsv").read_text().splitlines():
        i, t0, t1, code = line.split("\t")
        requests.append({"i": int(i), "t0": int(t0), "t1": int(t1), "code": code})
    if any(r["code"] != "200" for r in requests):
        invalid.append("a request did not return 200")

    by_trace = {}
    for span in spans:
        by_trace.setdefault(span["trace_id"], []).append(span)
    traces = [{"trace_id": t, "services": services_of(ss), "spans": len(ss)} for t, ss in by_trace.items()]
    traces.sort(key=lambda t: SERVICES.index(t["services"][0]) if t["services"] else 9)

    roots = {s: 0 for s in SERVICES}
    servers = {s: 0 for s in SERVICES}
    for span in spans:
        if not span["parent"]:
            roots[span["service"]] += 1
        if span["kind"] == SPAN_KIND_SERVER:
            servers[span["service"]] += 1

    n = len(requests)
    ratio = cond["sampler_ratio"] != "-"
    expected = {}
    if not ratio:
        if cond["envoy"] != "none":
            expected["envoy"] = n
        expected["a"] = n
        if cond["b"] != "manual":
            expected["b"] = n
        expected["c"] = n
    for service, count in expected.items():
        if servers[service] != count:
            invalid.append(f"{service}: {servers[service]} server spans, expected {count}")

    db_spans = [s for s in spans if s["service"] == "c" and s["name"].startswith("sql.")]
    c_server_traces = {s["trace_id"] for s in spans if s["service"] == "c" and s["kind"] == SPAN_KIND_SERVER}
    db_traces = {s["trace_id"] for s in db_spans}
    if not ratio and not db_spans:
        invalid.append("no DB span")
    c_server = {s["span_id"] for s in spans if s["service"] == "c" and s["kind"] == SPAN_KIND_SERVER}
    db_names = sorted({s["name"] for s in db_spans})
    db_parent_is_c_server = sum(1 for s in db_spans if s["parent"] in c_server)

    per_request = {}
    outside = 0
    for r in requests:
        per_request[r["i"]] = set()
    for span in spans:
        hit = next((r["i"] for r in requests if r["t0"] <= span["start_ns"] <= r["t1"]), None)
        if hit is None:
            outside += 1
        else:
            per_request[hit].add(span["service"])
    combos = {}
    for present in per_request.values():
        key = ", ".join(s for s in SERVICES if s in present)
        combos[key] = combos.get(key, 0) + 1
    if outside:
        invalid.append(f"{outside} spans outside every request window")

    comments = []
    for line in (out / "postgres.log").read_text().splitlines():
        m = COMMENT.search(line)
        if m:
            comments.append({"comment": m.group(0), "line": line})
    comment_trace_ids = sorted(
        {
            found
            for c in comments
            for pair in re.findall(r"traceparent='00-([0-9a-f]{32})-|x-b3-traceid='([0-9a-f]{32})'", c["comment"])
            for found in pair
            if found
        }
    )

    sent = {}
    sends = out / "sends.tsv"
    if sends.exists():
        for line in sends.read_text().splitlines():
            target, trace_id = line.split("\t")
            got = spans_in(out / "traces" / f"sent-{target}.json")
            sent[target] = {"trace_id": trace_id, "services": services_of(got), "spans": len(got)}

    result = {
        "condition": cond,
        "requests": n,
        "trace_count": len(by_trace),
        "traces": traces,
        "server_spans": servers,
        "root_spans": roots,
        "db_spans": len(db_spans),
        "db_span_names": db_names,
        "db_spans_with_c_server_parent": db_parent_is_c_server,
        "db_traces_without_c_server_span": sorted(db_traces - c_server_traces),
        "services_per_request": combos,
        "sql_comments": comments,
        "sql_comment_trace_ids": comment_trace_ids,
        "c_trace_ids": sorted(c_server_traces),
        "sent": sent,
        "invalid": invalid,
    }
    (out / "analysis.json").write_text(json.dumps(result, indent=1))


main(pathlib.Path(sys.argv[1]))
