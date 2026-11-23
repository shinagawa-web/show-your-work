import json
import sys

KEEP = {
    "aws_lb": lambda v: {"internal": v["internal"]},
    "aws_cognito_identity_provider": lambda v: {"provider_type": v["provider_type"]},
    "aws_verifiedaccess_endpoint": lambda v: {"endpoint_type": v["endpoint_type"]},
}
plan = json.load(sys.stdin)
out = []
for rc in plan["resource_changes"]:
    if rc["mode"] != "managed":
        continue
    entry = {"address": rc["address"], "type": rc["type"], "actions": rc["change"]["actions"]}
    if rc["type"] in KEEP:
        entry["values"] = KEEP[rc["type"]](rc["change"]["after"])
    out.append(entry)
json.dump(out, sys.stdout, indent=2)
sys.stdout.write("\n")
