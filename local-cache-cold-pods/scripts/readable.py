import json
import sys

op = float(sys.argv[1])
down = {}
seen = False
for line in sys.stdin:
    path, _, body = line.partition(":")
    r = json.loads(body)
    if r["t"] < op:
        continue
    if r["ev"] == "probe_down":
        down[path] = True
        seen = True
    elif r["ev"] == "probe_up":
        down[path] = False
sys.exit(0 if seen and not any(down.values()) else 1)
