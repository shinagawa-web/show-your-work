import json
import sys
from datetime import datetime, timezone

out = open(sys.argv[1], "a", buffering=1)
dec = json.JSONDecoder()
buf = ""
for chunk in iter(lambda: sys.stdin.readline(), ""):
    buf += chunk
    while True:
        s = buf.lstrip()
        if not s:
            buf = ""
            break
        try:
            obj, end = dec.raw_decode(s)
        except json.JSONDecodeError:
            break
        buf = s[end:]
        out.write(json.dumps({"recv": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "obj": obj}) + "\n")
