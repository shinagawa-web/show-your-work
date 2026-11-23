import collections
import json
import pathlib
import re
import sys

CONFIGS = [
    ("a-ip-allowlist", "A", 3, "IP allowlist on the internet-facing ALB"),
    ("b-alb-cognito", "B", 4, "ALB authentication with Cognito and Google Workspace SAML"),
    ("c-verified-access", "C", 5, "Verified Access in front of an internal ALB"),
]
FREE_RULES = 10
NEW_CONNECTIONS_PER_LCU_SECOND = 25
ACTIVE_CONNECTIONS_PER_LCU = 3000
GB_PER_LCU_HOUR = 1
RULE_EVALUATIONS_PER_LCU_SECOND = 1000

results = pathlib.Path(sys.argv[1])
here = pathlib.Path(sys.argv[2])
assumptions = json.loads((here / "assumptions.json").read_text())
prices_doc = json.loads((results / "prices" / "prices.json").read_text())
prices = prices_doc["prices"]
errors = []


def price(key):
    return float(prices[key]["usd"])


def num(x):
    if isinstance(x, int) or float(x).is_integer():
        return f"{int(x):,}"
    return f"{x:,.4f}".rstrip("0").rstrip(".")


def usd(x):
    return f"{x:,.2f}"


def unit_price(key):
    return prices[key]["usd"].rstrip("0").rstrip(".")


def load_plan(name):
    d = results / "plans" / name
    resources = json.loads((d / "resources.json").read_text())
    m = re.search(r"^Plan: (\d+) to add, (\d+) to change, (\d+) to destroy\.$", (d / "plan.log").read_text(), re.M)
    if not m:
        errors.append(f"{name}: no Plan: line in plan.log")
    elif int(m.group(1)) != len(resources) or m.group(2) != "0" or m.group(3) != "0":
        errors.append(f"{name}: {m.group(0)} does not match {len(resources)} resources")
    if any(r["actions"] != ["create"] for r in resources):
        errors.append(f"{name}: actions other than create")
    return {"creates": resources, "types": collections.Counter(r["type"] for r in resources), "line": m.group(0) if m else None, "name": name}


def json_documents(text):
    decoder, pos, docs = json.JSONDecoder(), 0, []
    text = text.strip()
    while pos < len(text):
        doc, pos = decoder.raw_decode(text, pos)
        docs.append(doc)
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return docs


def public_ipv4(name):
    merged = {}
    for doc in json_documents((results / name / "alb.json").read_text()):
        merged.update(doc)
    if merged["NetworkInterfaces"] != merged["AvailabilityZones"]:
        errors.append(f"{name}: {merged['NetworkInterfaces']} ALB network interfaces in {merged['AvailabilityZones']} Availability Zones")
    return merged["WithPublicIPv4"]


def probe_rows(*dirs):
    rows = []
    for d in dirs:
        f = results / d / "probes.jsonl"
        if f.exists():
            rows += [json.loads(line) for line in f.read_text().splitlines()]
    return rows


def probe_table(rows):
    lines = ["| Request | HTTP status | Redirect to host | App body returned |", "|---|--:|---|---|"]
    for r in rows:
        status = r["status"] if r["status"] != "000" else f"none (curl exit {r['curl_exit']})"
        lines.append(f"| {r['request']} | {status} | {r['location_host']} | {'yes' if r['app_body'] else 'no'} |")
    return lines


def claim_paths(claims, prefix=""):
    paths = []
    for k, v in claims.items():
        paths += claim_paths(v, f"{prefix}{k}.") if isinstance(v, dict) else [f"{prefix}{k}"]
    return paths


def observation_rows(name):
    rows = []
    for o in json.loads((results / "login" / "observations.json").read_text()):
        if o["config"] != name:
            continue
        measured = []
        if o["record"]:
            events = json.loads((results / "login" / name / f"{o['record']}.json").read_text())["events"]
            first = [e for e in events if e["path"] == "/"]
            if first:
                measured.append(f"`/` reached the app at {first[0]['timestamp']}")
            else:
                measured.append("no request reached the app (Lambda log)")
        if o["measured"]:
            measured.append(o["measured"])
        rows.append(f"| {o['setup']} | {'; '.join(measured)} | {o['reported']} | {o['inferred']} |")
    return ["| Setting | Measured | Reported | Inferred or not confirmed |", "|---|---|---|---|"] + rows


def arrived_rows(name, jwt_header, extra_headers):
    headers, claims = set(), []
    for o in json.loads((results / "login" / "observations.json").read_text()):
        if o["config"] != name or not o["record"]:
            continue
        for e in json.loads((results / "login" / name / f"{o['record']}.json").read_text())["events"]:
            headers |= set(e["header_names"])
            if jwt_header in e["decoded"]:
                for path in claim_paths(e["decoded"][jwt_header]):
                    if path not in claims:
                        claims.append(path)
    lines = ["| Header | Contents |", "|---|---|"]
    for h in [jwt_header, *extra_headers]:
        if h in headers:
            contents = ", ".join(f"`{c}`" for c in claims) if h == jwt_header else "present, value not recorded"
            lines.append(f"| `{h}` | {contents} |")
    return lines


def after(plan, rtype):
    return [r.get("values", {}) for r in plan["creates"] if r["type"] == rtype]


def costs(plan):
    hours = assumptions["hours_per_month"]
    req_per_s = assumptions["requests_per_month"] / (hours * 3600)
    lines = []
    lcu_detail = None
    rules = len(after(plan, "aws_lb_listener_rule"))
    for lb in after(plan, "aws_lb"):
        lines.append(("ALB hours", hours, "ALB-hour", "alb_hour"))
        dims = {
            "new_connections": req_per_s / NEW_CONNECTIONS_PER_LCU_SECOND,
            "active_connections": req_per_s * assumptions["connection_seconds"] / ACTIVE_CONNECTIONS_PER_LCU,
            "processed_bytes": assumptions["data_gb_per_month"] / hours / GB_PER_LCU_HOUR,
            "rule_evaluations": req_per_s * max(0, rules - FREE_RULES) / RULE_EVALUATIONS_PER_LCU_SECOND,
        }
        top = max(dims, key=dims.get)
        lcu_detail = {"requests_per_second": req_per_s, "listener_rules": rules, "lcu_by_dimension": dims, "largest": top}
        lines.append((f"ALB LCU ({top.replace('_', ' ')})", dims[top] * hours, "LCU-hour", "alb_lcu_hour"))
        if not lb["internal"]:
            lines.append(("ALB public IPv4 addresses", public_ipv4(plan["name"]) * hours, "IPv4 address-hour", "public_ipv4_hour"))
    saml = [p for p in after(plan, "aws_cognito_identity_provider") if p["provider_type"] == "SAML"]
    if saml:
        free = float(prices["cognito_saml_mau_free"]["end_range"])
        lines.append(("Cognito SAML users over the free tier", max(0, assumptions["employees"] - free), "MAU", "cognito_saml_mau"))
    endpoints = after(plan, "aws_verifiedaccess_endpoint")
    if endpoints:
        app_hours = assumptions["applications"] * hours
        end = prices["verified_access_app_hour"]["end_range"]
        if end != "Inf" and app_hours > float(end):
            errors.append(f"Verified Access app hours {app_hours} exceed the first tier ending at {end}")
        lines.append(("Verified Access applications", app_hours, "App-hour", "verified_access_app_hour"))
        lines.append(("Verified Access data processing", assumptions["data_gb_per_month"], "GB", "verified_access_gb"))
    rows = [(item, qty, unit, key, qty * price(key)) for item, qty, unit, key in lines]
    return rows, sum(r[4] for r in rows), lcu_detail


def endpoints_line(plan):
    eps = json.loads((results / "c-verified-access" / "endpoints.json").read_text())
    albs = [r["values"] for r in json.loads((results / "c-verified-access" / "resources.json").read_text()) if r["type"] == "aws_lb"]
    lb_arns = {e["LoadBalancerArn"] for e in eps}
    ok = (
        len(eps) == 3 and len(albs) == 1 and albs[0]["internal"] and lb_arns == {albs[0]["arn"]}
        and all(e["EndpointType"] == "load-balancer" and e["Status"] == "active" for e in eps)
    )
    if not ok:
        errors.append("endpoints.json does not show three active load-balancer endpoints on the one internal ALB")
    states = ", ".join(sorted({e["Status"] for e in eps}))
    return f"In the applied C, {len(eps)} Verified Access endpoints of type load-balancer were {states} and all pointed to {len(lb_arns)} internal ALB."


baseline = load_plan("baseline")
out = ["# internal-app-access", ""]
fetched = prices_doc["fetched_at"]
out += [
    "## Section 2: pricing assumptions",
    "",
    f"On-demand unit prices for ap-northeast-1 from the AWS Price List bulk files, fetched at {fetched}.",
    "",
    "| Item | Unit | USD |",
    "|---|---|--:|",
]
labels = {
    "alb_hour": ("ALB", "ALB-hour"),
    "alb_lcu_hour": ("ALB LCU", "LCU-hour"),
    "public_ipv4_hour": ("Public IPv4 address in use", "IPv4 address-hour"),
    "cognito_saml_mau": (f"Cognito SAML/OIDC federated user over the first {num(float(prices['cognito_saml_mau_free']['end_range']))} MAU", "MAU"),
    "verified_access_app_hour": (f"Verified Access application (first {num(float(prices['verified_access_app_hour']['end_range']))} hours)", "App-hour"),
    "verified_access_gb": ("Verified Access data processing", "GB"),
}
for key, (item, unit) in labels.items():
    out.append(f"| {item} | {unit} | {unit_price(key)} |")
out += ["", "Publication date of each price list file.", "", "| Offer | publicationDate |", "|---|---|"]
for code, o in prices_doc["offers"].items():
    out.append(f"| {code} | {o['publicationDate']} |")
out.append("")

def access_sections(name):
    if name == "a-ip-allowlist":
        lines = [
            "curl sent from the maintainer's host to the ALB of the applied A, before and after the allowed CIDR was narrowed.",
            "",
            *probe_table(probe_rows("a-ip-allowlist/probes")),
            "",
        ]
        f = results / "a-ip-allowlist" / "block-delay.txt"
        if f.exists():
            text = f.read_text()
            last = re.search(r"Last attempt that reached the app started ([+-][0-9.]+) s from the rule destruction", text)
            first = re.search(r"First attempt of the run that did not reach it .*?: ([+-][0-9.]+) s from the rule destruction.*; (\d+) attempts in a row", text)
            lines += [
                "curl every 2 s while the allowed CIDR was replaced once, with the end of `terraform apply`'s rule destruction as 0 s.",
                "",
                "| Attempt | Started (s) |",
                "|---|--:|",
                f"| Last one that reached the app | {last.group(1)} |",
                f"| First one that did not, followed by {int(first.group(2)) - 1} more that did not | {first.group(1)} |",
                "",
            ]
        return lines + ["Opening A in a browser.", "", *observation_rows(name), ""]
    if name == "b-alb-cognito":
        return [
            "curl sent from the maintainer's host to the ALB of the applied B without a session. The second request followed the first redirect once, while the identity provider still had the dummy SAML metadata.",
            "",
            *probe_table(probe_rows("b-alb-cognito/probes")),
            "",
            "Signing in to B in a browser, with the SAML metadata of a Google Workspace application.",
            "",
            *observation_rows(name),
            "",
            "Headers the app received from the ALB after the sign-ins above.",
            "",
            *arrived_rows(name, "x-amzn-oidc-data", ["x-amzn-oidc-accesstoken", "x-amzn-oidc-identity"]),
            "",
        ]
    return [
        "curl sent from the maintainer's host to the three Verified Access endpoints and to the internal ALB of the applied C, without signing in.",
        "",
        *probe_table(probe_rows("c-verified-access/recreate/probes") or probe_rows("c-verified-access/probes")),
        "",
        "Opening C (wiki) in a browser.",
        "",
        *observation_rows(name),
        "",
        "Headers the app received through Verified Access in the rows above where the request reached the app.",
        "",
        *arrived_rows(name, "x-amzn-ava-user-context", ["x-amzn-oidc-data"]),
        "",
    ]


summary = []
detail = {"assumptions": assumptions, "configs": {}}
for name, letter, section, title in CONFIGS:
    plan = load_plan(name)
    diff = plan["types"] - baseline["types"]
    removed = baseline["types"] - plan["types"]
    if removed:
        errors.append(f"{name}: resource types fewer than in the baseline: {dict(removed)}")
    rows, total, lcu = costs(plan)
    added = len(plan["creates"]) - len(baseline["creates"])
    summary.append((letter, title, added, total))
    detail["configs"][name] = {
        "plan_line": plan["line"],
        "resources": len(plan["creates"]),
        "added_over_baseline": added,
        "types_added": dict(diff),
        "lcu": lcu,
        "monthly": [{"item": r[0], "quantity": r[1], "unit": r[2], "price": r[3], "usd": r[4]} for r in rows],
        "monthly_total_usd": total,
    }
    out += [
        f"## Section {section}: {letter}, {title}",
        "",
        f"`terraform plan` of `terraform/baseline` and of `terraform/{name}`, each from an empty state. The baseline holds the ALB, the Lambda app behind it, a VPC and a self-signed certificate, and every configuration has the same.",
        "",
        "| Plan | Resources to add |",
        "|---|--:|",
        f"| baseline | {len(baseline['creates'])} |",
        f"| {letter} | {len(plan['creates'])} |",
        f"| {letter} minus baseline | {added} |",
        "",
        f"Resource types that {letter} adds over the baseline.",
        "",
        "| Resource type | Count |",
        "|---|--:|",
    ]
    for t, c in sorted(diff.items()):
        out.append(f"| `{t}` | {c} |")
    if letter == "C":
        without = load_plan("c-without-device-trust")
        out += [
            "",
            "The same plan with the device trust provider left off the Verified Access instance (`attach_device_trust = false`).",
            "",
            "| Plan | Resources to add |",
            "|---|--:|",
            f"| C without the device trust provider | {len(without['creates'])} |",
            f"| C without the device trust provider minus baseline | {len(without['creates']) - len(baseline['creates'])} |",
            "",
            endpoints_line(plan),
        ]
        detail["configs"]["c-without-device-trust"] = {"plan_line": without["line"], "resources": len(without["creates"])}
    out += [
        "",
        f"Monthly cost of {letter}, estimated for {assumptions['applications']} application from the unit prices in section 2 and `assumptions.json`. The resources above were counted in a plan with 3 applications.",
        "",
        "| Item | Quantity | Unit | Unit price (USD) | Monthly (USD) |",
        "|---|--:|---|--:|--:|",
    ]
    for item, qty, unit, key, cost in rows:
        out.append(f"| {item} | {num(round(qty, 2))} | {unit} | {unit_price(key)} | {usd(cost)} |")
    out += [f"| Total | | | | {usd(total)} |", ""]
    out += access_sections(name)

out += [
    "## Section 7: comparison",
    "",
    f"Resources each plan adds over the baseline plan, counted with 3 applications, and the monthly totals from sections 3 to 5, estimated for {assumptions['applications']} application.",
    "",
    "| Configuration | Resources added over the baseline | Monthly (USD) |",
    "|---|--:|--:|",
]
for letter, title, added, total in summary:
    out.append(f"| {letter}: {title} | {added} | {usd(total)} |")
out.append("")
detail["baseline"] = {"plan_line": baseline["line"], "resources": len(baseline["creates"]), "types": dict(baseline["types"])}

if errors:
    out += ["RUN INVALID", ""] + [f"- {e}" for e in errors] + [""]
(results / "summary.md").write_text("\n".join(out))
(results / "costs.json").write_text(json.dumps(detail, indent=2) + "\n")
if errors:
    print("\n".join(errors), file=sys.stderr)
    sys.exit(1)
