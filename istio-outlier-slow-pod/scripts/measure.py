import json
import math
import pathlib
import re
import sys
from collections import Counter
from datetime import datetime, timedelta

out = pathlib.Path(sys.argv[1])
rate = int(sys.argv[2])
window = int(sys.argv[3])


def ts(s):
    return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00")))


def p99(values):
    if not values:
        return None
    v = sorted(values)
    pos = 0.99 * (len(v) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (pos - lo), 1)


requests = []
active = []
dropped = 0
for line in (out / "k6.json").read_text().splitlines():
    rec = json.loads(line)
    if rec.get("type") != "Point":
        continue
    d = rec["data"]
    m = rec["metric"]
    if m == "http_req_duration" and d["tags"].get("ep") == "app":
        end = ts(d["time"])
        requests.append({"start": end - timedelta(milliseconds=d["value"]), "ms": d["value"], "status": d["tags"].get("status", ""), "error": d["tags"].get("error", "")})
    elif m == "od_ejections_active":
        active.append((ts(d["time"]), int(d["value"])))
    elif m == "dropped_iterations":
        dropped += int(d["value"])
active.sort()

first_on = next((t for t, v in active if v > 0), None)
first_off = next((t for t, v in active if first_on is not None and t > first_on and v == 0), None)

stats = {}
for line in (out / "stats-end.txt").read_text().splitlines():
    m = re.search(r"outlier_detection\.(ejections_enforced_total|ejections_overflow): (\d+)", line)
    if m:
        stats[m.group(1)] = int(m.group(2))

roles = {p["status"]["podIP"]: p["metadata"]["labels"]["role"] for p in json.loads((out / "pods.json").read_text())["items"]}

endpoint_mid = (out / "endpoint-mid.txt").read_text()
ejected = re.findall(r"^([\d.]+):8080\s+\S+\s+FAILED\s", endpoint_mid, re.M)

served = {}
logged = 0
last_to_ejected = None
for line in (out / "proxy.log").read_text().splitlines():
    if not line.startswith("{"):
        continue
    rec = json.loads(line)
    if "app.demo.svc.cluster.local" not in (rec.get("upstream_cluster") or ""):
        continue
    logged += 1
    ip = (rec.get("upstream_host") or "").split(":")[0]
    served.setdefault(roles.get(ip, "none"), Counter())[f"{rec.get('response_code')} {rec.get('response_flags')}"] += 1
    start = ts(rec["start_time"])
    if ip in ejected and (first_off is None or start < first_off):
        last_to_ejected = max(last_to_ejected or start, start)

traffic_off = max(first_on, last_to_ejected) if first_on is not None and last_to_ejected is not None else first_on
during = [r["ms"] for r in requests if traffic_off is not None and traffic_off < r["start"] and (first_off is None or r["start"] < first_off)]

problems = []
if dropped:
    problems.append(f"k6 dropped {dropped} iterations")
if abs(len(requests) - rate * window) > rate:
    problems.append(f"k6 sent {len(requests)} requests, expected {rate * window}")
if logged != len(requests):
    problems.append(f"the access log has {logged} requests, k6 sent {len(requests)}")
if len(active) < window * 4:
    problems.append(f"only {len(active)} polls of the Envoy admin stats")
if len(stats) != 2:
    problems.append("outlier_detection stats missing")
if "OUTLIER CHECK" not in endpoint_mid:
    problems.append("endpoint output has no OUTLIER CHECK column")
if len(re.findall(r"^[\d.]+:8080\s", endpoint_mid, re.M)) != 3:
    problems.append("endpoint output does not have 3 endpoints")

json.dump({
    "stats": stats,
    "p99_ms": p99([r["ms"] for r in requests]),
    "k6_outcomes": [{"status": k[0], "error": k[1], "requests": n} for k, n in sorted(Counter((r["status"], r["error"]) for r in requests).items())],
    "p99_while_ejected_ms": p99(during),
    "ejected_roles": [roles.get(ip, "unknown") for ip in ejected],
    "served": {k: dict(v) for k, v in sorted(served.items())},
    "problems": problems,
}, sys.stdout, indent=1)
print()
