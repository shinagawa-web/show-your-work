import argparse
import base64
import datetime
import json
import pathlib
import re
import subprocess

TOKEN_HEADERS = {"x-amzn-oidc-accesstoken", "x-amzn-oidc-identity", "x-amzn-oidc-data", "x-amzn-ava-user-context", "cookie"}
JWT_HEADERS = {"x-amzn-oidc-data", "x-amzn-ava-user-context"}
GROUP_CLAIMS = {"custom:groups", "groups"}
SHOWN_CLAIMS = {"email", "address", "verified", "custom:groups", "groups", "identities", "username", "iss", "exp", "aud"}

p = argparse.ArgumentParser()
p.add_argument("--log-group", required=True)
p.add_argument("--since", required=True)
p.add_argument("--operated-at", required=True)
p.add_argument("--label", required=True)
p.add_argument("--out", required=True)
p.add_argument("--note", default="")
p.add_argument("--mask", action="append", default=[])
args = p.parse_args()
masks = [m.split("=", 1) for m in args.mask]


def mask(text):
    for old, new in masks:
        text = text.replace(old, new)
    text = re.sub(r"(GoogleWorkspace_)?[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", lambda m: (m.group(1) or "") + "user@example.com", text)
    text = re.sub(r"identitystore_id=d-[0-9a-f]+", "identitystore_id=d-0000000000", text)
    text = re.sub(r"idpid=[A-Za-z0-9]+", "idpid=C00000000", text)
    return re.sub(r"\bd-[0-9a-f]{10}\b", "d-0000000000", text)


def jwt_payload(token):
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


def group_names(v):
    if isinstance(v, list):
        return ["<group-name>" for _ in v]
    return ",".join("<group-name>" for _ in str(v).split(","))


def claim_view(claims):
    out = {}
    for k, v in claims.items():
        if isinstance(v, dict):
            out[k] = claim_view(v)
        elif k in GROUP_CLAIMS:
            out[k] = group_names(v)
        elif k in SHOWN_CLAIMS:
            out[k] = v
        else:
            out[k] = "<redacted>"
    return out


since = datetime.datetime.fromisoformat(args.since.replace("Z", "+00:00"))
r = subprocess.run(
    ["aws", "logs", "filter-log-events", "--log-group-name", args.log_group,
     "--start-time", str(int(since.timestamp() * 1000)), "--output", "json"],
    capture_output=True, text=True, check=True,
)
events = json.loads(r.stdout)["events"]

records = []
lines = [f"Operated by the user at: {args.operated_at}", f"Log group: {args.log_group}", f"Log events since: {args.since}"]
if args.note:
    lines.append(f"Note: {args.note}")
lines.append("")
for e in events:
    msg = e["message"].strip()
    if not msg.startswith("{"):
        continue
    body = json.loads(msg)
    headers = body.get("headers", {})
    decoded = {}
    shown = {}
    for name, value in headers.items():
        if name in JWT_HEADERS:
            try:
                decoded[name] = claim_view(jwt_payload(value))
            except Exception as ex:
                decoded[name] = f"could not decode: {ex}"
        if name in TOKEN_HEADERS:
            shown[name] = "<redacted>"
        elif name == "x-forwarded-for":
            shown[name] = "<client address>"
        else:
            shown[name] = value
    body["headers"] = shown
    ts = datetime.datetime.fromtimestamp(e["timestamp"] / 1000, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines.append(f"--- log event at {ts} (header values of {', '.join(sorted(TOKEN_HEADERS))} redacted)")
    lines.append(json.dumps(body, indent=2))
    for name, claims in decoded.items():
        lines.append(f"--- decoded payload of {name} (values other than {', '.join(sorted(SHOWN_CLAIMS))} redacted)")
        lines.append(json.dumps(claims, indent=2))
    lines.append("")
    records.append({
        "timestamp": ts,
        "host": headers.get("host"),
        "path": body.get("path"),
        "header_names": sorted(headers),
        "decoded": decoded,
    })

out = pathlib.Path(args.out)
out.mkdir(parents=True, exist_ok=True)
(out / f"{args.label}.txt").write_text(mask("\n".join(lines)) + "\n")
(out / f"{args.label}.json").write_text(mask(json.dumps({
    "label": args.label, "operated_at": args.operated_at, "note": args.note, "events": records,
}, indent=2)) + "\n")
print(mask(f"{args.label}: {len(records)} requests logged"))
