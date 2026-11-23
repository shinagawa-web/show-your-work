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


def load_applied(name):
    d = results / name
    missing = [f for f in ("resources.json", "apply.log", "destroy.log") if not (d / f).exists()]
    if missing:
        errors.append(f"{name}: no {', '.join(missing)}")
        return None
    resources = json.loads((d / "resources.json").read_text())
    apply_log = (d / "apply.log").read_text()
    destroy_log = (d / "destroy.log").read_text()
    m = re.search(r"^Apply complete! Resources: (\d+) added, (\d+) changed, (\d+) destroyed\.$", apply_log, re.M)
    line = m.group(0) if m else None
    if not m:
        errors.append(f"{name}: no Apply complete! line in apply.log")
    elif int(m.group(1)) != len(resources) or m.group(2) != "0" or m.group(3) != "0":
        errors.append(f"{name}: {line} does not match {len(resources)} resources in the state")
    md = re.search(r"^Destroy complete! Resources: (\d+) destroyed\.$", destroy_log, re.M)
    if not md or int(md.group(1)) != len(resources):
        errors.append(f"{name}: destroy.log does not show {len(resources)} resources destroyed")
    types = collections.Counter(r["type"] for r in resources)
    return {"creates": resources, "types": types, "line": line, "dir": d}


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
            lines.append(("ALB public IPv4 addresses", len(lb["subnets"]) * hours, "IPv4 address-hour", "public_ipv4_hour"))
    saml = [p for p in after(plan, "aws_cognito_identity_provider") if p["provider_type"] == "SAML"]
    if saml:
        free = float(prices["cognito_saml_mau_free"]["end_range"])
        lines.append(("Cognito SAML users over the free tier", max(0, assumptions["employees"] - free), "MAU", "cognito_saml_mau"))
    endpoints = after(plan, "aws_verifiedaccess_endpoint")
    if endpoints:
        app_hours = len(endpoints) * hours
        end = prices["verified_access_app_hour"]["end_range"]
        if end != "Inf" and app_hours > float(end):
            errors.append(f"Verified Access app hours {app_hours} exceed the first tier ending at {end}")
        lines.append(("Verified Access applications", app_hours, "App-hour", "verified_access_app_hour"))
        lines.append(("Verified Access data processing", assumptions["data_gb_per_month"], "GB", "verified_access_gb"))
    rows = [(item, qty, unit, key, qty * price(key)) for item, qty, unit, key in lines]
    return rows, sum(r[4] for r in rows), lcu_detail


def endpoints_line(plan):
    f = plan["dir"] / "endpoints.json"
    if not f.exists():
        errors.append("c-verified-access: no endpoints.json")
        return "No endpoints.json."
    eps = json.loads(f.read_text())
    albs = after(plan, "aws_lb")
    lb_arns = {e["LoadBalancerArn"] for e in eps}
    ok = (
        len(eps) == 3
        and len(albs) == 1
        and albs[0]["internal"]
        and lb_arns == {albs[0]["arn"]}
        and all(e["EndpointType"] == "load-balancer" and e["Status"] == "active" for e in eps)
    )
    if not ok:
        errors.append(f"endpoints.json does not show three active load-balancer endpoints on the one internal ALB: {eps}")
    states = ", ".join(sorted({e["Status"] for e in eps}))
    return (
        f"Verified Access endpoints of type load-balancer created: {len(eps)}, status {states}, "
        f"pointing to {len(lb_arns)} load balancer ({'the internal ALB of this apply' if ok else 'see endpoints.json'})."
    )


baseline = load_applied("baseline")
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

summary = []
detail = {"assumptions": assumptions, "configs": {}}
for name, letter, section, title in CONFIGS:
    plan = load_applied(name)
    if plan is None or baseline is None:
        continue
    diff = plan["types"] - baseline["types"]
    removed = baseline["types"] - plan["types"]
    if removed:
        errors.append(f"{name}: resource types fewer than in the baseline: {dict(removed)}")
    rows, total, lcu = costs(plan)
    added = len(plan["creates"]) - len(baseline["creates"])
    summary.append((letter, title, added, total))
    detail["configs"][name] = {
        "apply_line": plan["line"],
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
        f"Resources created by `terraform apply` of `terraform/baseline` (the ALB module) and of `terraform/{name}`, in us-east-1. Both include the same VPC and imported self-signed certificate (`terraform/modules/base`).",
        "",
        "| Apply | Resources created |",
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
        out += ["", endpoints_line(plan)]
    out += [
        "",
        f"Monthly cost of {letter}, from the resources its apply created, the unit prices in section 2 and `assumptions.json`.",
        "",
        "| Item | Quantity | Unit | Unit price (USD) | Monthly (USD) |",
        "|---|--:|---|--:|--:|",
    ]
    for item, qty, unit, key, cost in rows:
        out.append(f"| {item} | {num(round(qty, 2))} | {unit} | {unit_price(key)} | {usd(cost)} |")
    out += [f"| Total | | | | {usd(total)} |", ""]

out += [
    "## Section 7: comparison",
    "",
    "Resources each apply created over the baseline apply, and the monthly totals from sections 3 to 5.",
    "",
    "| Configuration | Resources added over the baseline | Monthly (USD) |",
    "|---|--:|--:|",
]
for letter, title, added, total in summary:
    out.append(f"| {letter}: {title} | {added} | {usd(total)} |")
out.append("")
if baseline is not None:
    detail["baseline"] = {"apply_line": baseline["line"], "resources": len(baseline["creates"]), "types": dict(baseline["types"])}

if errors:
    out += ["RUN INVALID", ""] + [f"- {e}" for e in errors] + [""]
(results / "summary.md").write_text("\n".join(out))
(results / "costs.json").write_text(json.dumps(detail, indent=2) + "\n")
if errors:
    print("\n".join(errors), file=sys.stderr)
    sys.exit(1)
