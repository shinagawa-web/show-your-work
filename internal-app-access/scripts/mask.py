import os
import re
import sys

LITERALS = [
    (os.environ.get("MASK_ACCOUNT", ""), "111122223333"),
    (os.environ.get("MASK_ASSUMED_ROLE", ""), "assumed-role/verify/verify"),
    (os.environ.get("MASK_HOST_IP", ""), "198.51.100.1"),
    (os.environ.get("MASK_GROUP_ID", ""), "<verifiers-group-id>"),
    (os.environ.get("MASK_PARENT_DOMAIN", ""), "example.com"),
]
PEM_LINE = re.compile(r"^[\s+~|-]*[A-Za-z0-9+/=]{16,}\s*$")
in_certificate = False


def mask_line(line):
    for old, new in LITERALS:
        if old:
            line = line.replace(old, new)
    line = re.sub(r"identitystore_id=d-[0-9a-f]*", "identitystore_id=d-0000000000", line)
    line = re.sub(r"\b(?:AROA|AIDA|AGPA|ANPA|ANVA|ASCA)[A-Z0-9]{17}(?![A-Z0-9])", "<unique-id>", line)
    line = re.sub(r"(-----BEGIN CERTIFICATE-----)(?:\\[rn])+.*?(?:\\[rn])+(-----END CERTIFICATE-----)", r"\1\\n<certificate>\\n\2", line)
    return re.sub(r"(<(?:\w+:)?X509Certificate>)[^<]+", r"\1<certificate>", line)


for line in sys.stdin:
    if in_certificate and PEM_LINE.match(line):
        continue
    in_certificate = False
    out = mask_line(line)
    stripped = out.rstrip("\n")
    if stripped.endswith("-----BEGIN CERTIFICATE-----"):
        in_certificate = True
        indent = stripped[: len(stripped) - len("-----BEGIN CERTIFICATE-----")]
        out += f"{indent}<certificate>\n"
    sys.stdout.write(out)
    sys.stdout.flush()
