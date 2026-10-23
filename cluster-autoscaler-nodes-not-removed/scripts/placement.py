import json
import sys
from collections import Counter

sel = sys.argv[1]
pods = [p for p in json.load(sys.stdin)["items"] if not p["metadata"].get("deletionTimestamp")]
count = Counter(p["spec"].get("nodeName", "") for p in pods)
print(f"{sel}: " + " ".join(f"{n}={count[n]}" for n in sorted(count)))
sys.exit(0 if len(pods) == 3 and all(v == 1 and n.startswith("ng-a-") for n, v in count.items()) else 1)
