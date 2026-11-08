import json
import pathlib
import re
import sys

results = pathlib.Path(sys.argv[1])
conds = sys.argv[2:]
params = dict(re.findall(r"^(\w+)=(\S+)$", (results.parent / "scripts" / "params.env").read_text(), re.M))
versions = dict(re.findall(r"^(\w+)=(\S+)$", (results.parent / "scripts" / "versions.env").read_text(), re.M))
ROLE = {"normal": "normal", "bad": "slow"}

data = {}
invalid = []
for c in conds:
    d = results / c
    if (d / "invalid").exists():
        invalid.append(f"{c}: {(d / 'invalid').read_text().strip()}")
    if not (d / "metrics.json").exists():
        invalid.append(f"{c}: no metrics.json")
        continue
    m = json.loads((d / "metrics.json").read_text())
    m["env"] = dict(re.findall(r"^(\w+)=(\S+)$", (d / "cond.env").read_text(), re.M))
    invalid.extend(f"{c}: {p}" for p in m["problems"])
    data[c] = m

for line in invalid:
    print(f"RUN INVALID: {line}")
if invalid:
    print()

window = params["WINDOW"]
rate = params["RATE"]
print("# istio-outlier-slow-pod")
print()
envoy = next((m["envoy_version"].split("/")[1] for m in data.values()), "unknown")
print(f"Istio {versions['ISTIO_VERSION']} (Envoy {envoy}) with sidecars on kind. Three Pods behind one Service and one caller Pod with a sidecar. "
      f"k6 in the caller Pod sends {rate} requests per second for {window} seconds. "
      f"Normal Pods respond in {params['NORMAL_MS']} ms and the slow Pod in {params['SLOW_MS']} ms. "
      "The DestinationRule sets `consecutive5xxErrors: 5` and leaves the rest at the defaults, except `maxEjectionPercent` where a table gives it. "
      f"Each row is the result of one run of its condition, from a new caller Pod, so the counters start at 0. The counters are read from the caller's sidecar at the end of the {window} seconds. "
      f"The {window} seconds are shorter than the default `baseEjectionTime` of 30 seconds, so these results do not cover a Pod after it returns.")
print()

rows31 = [c for c in ("5xx-default", "5xx-34", "slow-34", "slow-client-200ms", "slow-body-200ms") if c in data]
if rows31:
    print("## Section 3-1: what is ejected (job `3-1`)")
    print()
    print(f"One Pod of three returns 500 or is slow, and the other two are normal. Counts over the {window} seconds of load.")
    print()
    print("| Pod state | Timeout | `maxEjectionPercent` | `ejections_enforced_total` | `ejections_overflow` |")
    print("|---|---|---|---:|---:|")
    for c in rows31:
        m = data[c]
        e = m["env"]
        state = "returns 500" if e["bad_status"] == "500" else "headers at once, body slow" if e["bad_body_delay"] != "0" else "slow only"
        timeout = f"k6 HTTP client only ({e['client_timeout']})" if e["client_timeout"] != "none" else f"route ({e['route_timeout']})" if e["route_timeout"] != "none" else "none"
        pct = "default (10)" if e["max"] == "default" else e["max"]
        print(f"| {state} | {timeout} | {pct} | {m['stats']['ejections_enforced_total']} | {m['stats']['ejections_overflow']} |")
    print()
    if "slow-34" in data:
        m = data["slow-34"]
        print(f"In the third row, the p99 of all {m['requests']} requests in the {window} seconds was {m['p99_ms']} ms.")
        print()
    if "slow-client-200ms" in data:
        m = data["slow-client-200ms"]
        cut = m["status"].get("0", 0)
        dc = sum(n for codes in m["served"].values() for code, n in codes.items() if code.split()[-1] == "DC")
        print(f"In the fourth row, k6 cut {cut} requests at 200 ms, and the access log of the caller's sidecar has {dc} requests with the response flag `DC`, which means the downstream client closed the connection before the response.")
        print()

    if "slow-body-200ms" in data:
        m = data["slow-body-200ms"]
        seen = ", ".join(f"`{o['error']}` on {o['requests']} requests" for o in m["k6_outcomes"] if o["status"] != "200")
        cut = m["served"].get("bad", {}).get("200 UT", 0)
        print(f"In the fifth row, k6 saw {seen} and {m['status'].get('504', 0)} 504 responses, and the access log of the caller's sidecar has {cut} requests with response code 200 and the response flag `UT`, which means the route timeout cut the response after the headers.")
        print()

rows32 = [c for c in ("route-200ms", "route-1000ms") if c in data]
if rows32:
    print("## Section 3-2: the route timeout decides what is ejected (job `3-2`)")
    print()
    print(f"One Pod of three is slow, `maxEjectionPercent` is 34, and a VirtualService sets the route timeout. "
          f"k6 started the load {params['QUIET']} seconds after the Pods were ready. Counts are over the {window} seconds of load. "
          "The ejected Pod is the one with the `failed_outlier_check` flag in the caller's Envoy at second "
          f"{params['MID']}. The p99 is over the requests that started after the caller's sidecar sent its last request to the ejected Pod, until the end of the load.")
    print()
    print("| Timeout | Ejected Pod | 504 responses | p99 while ejected |")
    print("|---|---|---:|---:|")
    for c in rows32:
        m = data[c]
        ejected = ", ".join(ROLE.get(r, r) for r in m["ejected_roles"]) or "none"
        p = m["p99_while_ejected_ms"]
        while_ejected = f"{p} ms" if p is not None else "not ejected"
        print(f"| {m['env']['route_timeout']} | {ejected} | {m['status'].get('504', 0)} | {while_ejected} |")
    print()

if "route-200ms" in data:
    d = results / "route-200ms"
    m = data["route-200ms"]
    total = m["status"].get("504", 0)
    to_slow = m["served"].get("bad", {}).get("504 UT", 0)
    if to_slow == total:
        print(f"In the 200ms row the caller's sidecar made all {total} 504 responses by cutting its requests to the slow Pod at 200 ms.")
    else:
        print(f"In the 200ms row the caller's sidecar made {to_slow} of the {total} 504 responses by cutting its requests to the slow Pod at 200 ms.")
    print()
    print(f"`istioctl proxy-config endpoint` for the 200ms row at second {params['MID']} of the load:")
    print()
    print("```")
    print((d / "endpoint-mid.txt").read_text().rstrip())
    print("```")
    print()
    slow_ip = next(p["status"]["podIP"] for p in json.loads((d / "pods.json").read_text())["items"] if p["metadata"]["labels"]["role"] == "bad")
    print(f"`{slow_ip}` is the slow Pod.")
