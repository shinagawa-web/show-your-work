import argparse
import ipaddress
import json
import pathlib
import re
import shlex
import socket
import subprocess
import tempfile
import time
import urllib.parse

APP_BODY = "reached app"

p = argparse.ArgumentParser()
p.add_argument("--out", required=True)
p.add_argument("--name", required=True)
p.add_argument("--request", required=True)
p.add_argument("--prediction", required=True)
p.add_argument("--url", required=True)
p.add_argument("--connect-to")
p.add_argument("--wait-dns", default="")
p.add_argument("--private-only", action="store_true")
p.add_argument("--retry-seconds", type=int, default=0)
p.add_argument("--until-no-body-seconds", type=int, default=0)
p.add_argument("--location-file")
p.add_argument("--mask", action="append", default=[])
args = p.parse_args()

out = pathlib.Path(args.out)
out.mkdir(parents=True, exist_ok=True)
masks = [m.split("=", 1) for m in args.mask]


def mask(text):
    for old, new in masks:
        text = text.replace(old, new)
    text = re.sub(r"identitystore_id=d-[0-9a-f]+", "identitystore_id=d-0000000000", text)
    text = re.sub(r"\b(request_uri|state|SAMLRequest|RelayState|nonce)=[^&\s\"]+", r"\1=<redacted>", text)
    return re.sub(r"(?i)(set-cookie: [A-Za-z0-9_-]+=)[^;\r\n]*", r"\1<redacted>", text)


lines = [f"Request: {args.request}", f"Prediction: {args.prediction}", ""]
resolved = []
if args.wait_dns:
    start = time.monotonic()
    while True:
        try:
            resolved = sorted({ai[4][0] for ai in socket.getaddrinfo(args.wait_dns, 443, socket.AF_INET)})
            break
        except socket.gaierror as e:
            if time.monotonic() - start > 300:
                lines.append(f"{args.wait_dns} did not resolve within 300 s: {e}")
                break
            time.sleep(5)
    lines.append(f"$ python3 -c 'socket.getaddrinfo(\"{args.wait_dns}\", 443, socket.AF_INET)'")
    lines.append(" ".join(resolved) if resolved else "(no address)")
    if resolved:
        private = all(ipaddress.ip_address(a).is_private for a in resolved)
        lines.append(f"All addresses private: {private}")
    lines.append("")

cmd = ["curl", "-sS", "-k", "--max-time", "10", "-o", "BODY", "-D", "HEADERS", "-w", r"%{http_code} %{exitcode}\n"]
if args.connect_to:
    cmd += ["--connect-to", args.connect_to]
cmd.append(args.url)

attempts = 0
start = time.monotonic()
with tempfile.TemporaryDirectory() as tmp:
    body_f = pathlib.Path(tmp) / "body"
    headers_f = pathlib.Path(tmp) / "headers"
    run = [str(body_f) if c == "BODY" else str(headers_f) if c == "HEADERS" else c for c in cmd]
    history = []
    while True:
        attempts += 1
        body_f.write_bytes(b"")
        headers_f.write_bytes(b"")
        r = subprocess.run(run, capture_output=True, text=True)
        body = body_f.read_text(errors="replace")
        headers = headers_f.read_text(errors="replace")
        elapsed = time.monotonic() - start
        history.append(f"{elapsed:6.1f}s {r.stdout.strip()} app_body={APP_BODY in body}")
        if args.until_no_body_seconds:
            if APP_BODY not in body or elapsed >= args.until_no_body_seconds:
                break
            time.sleep(2)
            continue
        if APP_BODY in body or elapsed >= args.retry_seconds:
            break
        time.sleep(10)

status, exitcode = (r.stdout.split() + ["", ""])[:2]
location = ""
for h in headers.splitlines():
    if h.lower().startswith("location:"):
        location = h.split(":", 1)[1].strip()
if args.location_file:
    pathlib.Path(args.location_file).write_text(location)
host = (urllib.parse.urlsplit(location).hostname or "") if location else ""
reached = APP_BODY in body

lines.append("$ " + shlex.join(cmd).replace("BODY", "body").replace("HEADERS", "headers"))
lines.append(f"(attempts: {attempts})")
if len(history) > 1:
    lines.append("--- attempts (seconds since the first attempt, http_code exitcode)")
    lines += history
lines.append("--- write-out (http_code exitcode)")
lines.append(r.stdout.rstrip())
if r.stderr.strip():
    lines.append("--- stderr")
    lines.append(r.stderr.rstrip())
lines.append("--- headers")
lines.append(headers.rstrip())
lines.append("--- body")
lines.append(body.rstrip())
lines.append("")
(out / f"{args.name}.txt").write_text(mask("\n".join(lines)) + "\n")

row = {
    "name": args.name,
    "request": args.request,
    "prediction": args.prediction,
    "status": status,
    "curl_exit": exitcode,
    "location_host": host,
    "location": location,
    "app_body": reached,
    "body": body.strip()[:200],
    "resolved": resolved,
    "attempts": attempts,
    "seconds_to_last_attempt": round(time.monotonic() - start, 1),
}
with (out / "probes.jsonl").open("a") as f:
    f.write(mask(json.dumps(row)) + "\n")
print(mask(f"{args.name}: status={status} curl_exit={exitcode} location_host={host} app_body={reached}"))
