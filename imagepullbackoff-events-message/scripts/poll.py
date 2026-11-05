import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ns, out_path, stop = sys.argv[1], sys.argv[2], sys.argv[3]
last = {}
with open(out_path, "a", buffering=1) as out:
    while not os.path.exists(stop):
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        try:
            pods = json.loads(subprocess.run(["kubectl", "get", "pods", "-n", ns, "-o", "json"], capture_output=True, text=True, check=True).stdout)
            table = subprocess.run(["kubectl", "get", "pods", "-n", ns, "--no-headers"], capture_output=True, text=True, check=True).stdout
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            time.sleep(1)
            continue
        rows = {}
        for line in table.splitlines():
            f = line.split()
            if f:
                rows[f[0]] = f[2]
        for p in pods["items"]:
            n = p["metadata"]["name"]
            cs = (p.get("status", {}).get("containerStatuses") or [{}])[0]
            rec = {
                "status": rows.get(n),
                "phase": p.get("status", {}).get("phase"),
                "state": cs.get("state"),
                "lastState": cs.get("lastState"),
                "restartCount": cs.get("restartCount"),
            }
            if last.get(n) != rec:
                last[n] = rec
                out.write(json.dumps({"t": now, "pod": n, **rec}) + "\n")
        time.sleep(1)
