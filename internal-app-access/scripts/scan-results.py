import base64
import os
import pathlib
import re
import sys
import urllib.parse

literals = {
    name: os.environ.get(var, "")
    for name, var in [
        ("account ID", "MASK_ACCOUNT"), ("role", "SCAN_ROLE"), ("host IP", "MASK_HOST_IP"),
        ("group ID", "MASK_GROUP_ID"), ("parent domain", "MASK_PARENT_DOMAIN"),
    ]
}
patterns = {
    "private key": r"PRIVATE KEY",
    "access key": r"(ASIA|AKIA)[A-Z0-9]{16}",
    "unique ID": r"(?:AROA|AIDA|AGPA|ANPA|ANVA|ASCA)[A-Z0-9]{17}(?![A-Z0-9])",
    "JWT": r"eyJ[A-Za-z0-9_-]{10}",
    "local path": r"/(private|Users|var/folders)/",
    "certificate": r"-----BEGIN CERTIFICATE-----(?:\\[rn]|\s)+[+~|\-\s]*[A-Za-z0-9+/=]{16,}|X509Certificate>[A-Za-z0-9+/=]{16,}",
    "Identity Center store ID": r"(?<![A-Za-z0-9])d-(?!0{10})[0-9a-f]{10}(?![0-9a-f])",
    "idpid": r"idpid=(?!C00000000)[A-Za-z0-9]+",
}
EMAIL = re.compile(rb"[A-Za-z0-9._%+-]{2,}@(?:[A-Za-z0-9-]{2,}\.)+[A-Za-z]{2,}")
RUN = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")


def decoded(text, depth=2):
    texts = {text, urllib.parse.unquote(text)}
    for t in list(texts):
        texts.add(re.sub(r"\\[rn]", "", t))
        texts.add(re.sub(r"(?<=[A-Za-z0-9+/])\r?\n[\s+~|-]*(?=[A-Za-z0-9+/=])", "", t))
    seen = set()
    for t in texts:
        for run in RUN.findall(t):
            run = run.rstrip("=").replace("-", "+").replace("_", "/")
            for offset in range(4):
                part = run[offset:]
                part = part[: len(part) - len(part) % 4] if len(part) % 4 == 1 else part
                if len(part) < 12 or part in seen:
                    continue
                seen.add(part)
                try:
                    raw = base64.b64decode(part + "=" * (-len(part) % 4))
                except ValueError:
                    continue
                yield raw
                if depth > 1:
                    yield from decoded(raw.decode("latin-1"), depth - 1)


def check(text):
    for name, value in literals.items():
        if value and value in text:
            yield name
    for name, pattern in patterns.items():
        if re.search(pattern, text):
            yield name
    for raw in decoded(text):
        for name in ("account ID", "parent domain"):
            if literals[name] and literals[name].encode() in raw:
                yield f"{name} in base64"
        if any(m != b"user@example.com" for m in EMAIL.findall(raw)):
            yield "e-mail address in base64"


root = pathlib.Path(sys.argv[1])
for f in sorted(root.rglob("*")):
    if f.is_file():
        for name in sorted(set(check(f.read_text(errors="replace")))):
            print(f"{f}: {name}")
