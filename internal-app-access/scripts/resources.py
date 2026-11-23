import json
import sys

KEEP = {
    "aws_lb": lambda v: {"name": v["name"], "internal": v["internal"], "subnets": v["subnets"], "arn": v["arn"]},
    "aws_cognito_identity_provider": lambda v: {"provider_type": v["provider_type"]},
    "aws_verifiedaccess_endpoint": lambda v: {
        "application_domain": v["application_domain"],
        "endpoint_type": v["endpoint_type"],
        "load_balancer_arn": v["load_balancer_options"][0]["load_balancer_arn"],
    },
}


def walk(module):
    for r in module.get("resources", []):
        if r["mode"] == "managed":
            yield r
    for child in module.get("child_modules", []):
        yield from walk(child)


state = json.load(sys.stdin)
out = []
for r in walk(state.get("values", {}).get("root_module", {})):
    entry = {"address": r["address"], "type": r["type"]}
    if r["type"] in KEEP:
        entry["values"] = KEEP[r["type"]](r["values"])
    out.append(entry)
json.dump(out, sys.stdout, indent=2)
sys.stdout.write("\n")
