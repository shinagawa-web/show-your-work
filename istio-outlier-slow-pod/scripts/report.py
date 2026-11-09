import json
import pathlib
import re
import sys

results = pathlib.Path(sys.argv[1])
conds = sys.argv[2:]
params = dict(re.findall(r"^(\w+)=(\S+)$", (results.parent / "scripts" / "params.env").read_text(), re.M))
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


def ms(v):
    return v.removesuffix("ms")


def failed(m):
    k6 = ", ".join(f"`{o['error'] or o['status']}`" for o in m["k6_outcomes"] if o["status"] != "200")
    k6_n = sum(o["requests"] for o in m["k6_outcomes"] if o["status"] != "200")
    codes = {code: n for code, n in m["served"].get("bad", {}).items() if code != "200 -"}
    log = ", ".join(f"`{code.split()[1]}`" if code.startswith("0 ") else f"`{code}`" for code in codes)
    return k6, k6_n, log, sum(codes.values())


window = params["WINDOW"]
print("# istio-outlier-slow-pod")
print()
print(f"One run per condition. Three Pods behind one Service: one is slow ({params['SLOW_MS']} ms) or returns 500, the other two respond in {params['NORMAL_MS']} ms. "
      f"k6 in one caller Pod sends {params['RATE']} requests/s for {window} s. `consecutive5xxErrors: 5`. Counters are from the caller's sidecar at the end of the {window} s.")
print()

rows31 = [c for c in ("5xx-default", "5xx-34", "slow-34", "slow-client-200ms", "slow-body-200ms") if c in data]
if rows31:
    print("## 3-1. What is ejected (job `3-1`)")
    print()
    print("| Pod state | Timeout | `maxEjectionPercent` (%) | `ejections_enforced_total` | `ejections_overflow` |")
    print("|---|---|---:|---:|---:|")
    for c in rows31:
        e = data[c]["env"]
        s = data[c]["stats"]
        state = "returns 500" if e["bad_status"] == "500" else "headers at once, body slow" if e["bad_body_delay"] != "0" else "slow"
        timeout = f"k6 client {ms(e['client_timeout'])} ms" if e["client_timeout"] != "none" else f"route {ms(e['route_timeout'])} ms" if e["route_timeout"] != "none" else "none"
        pct = "10 (default)" if e["max"] == "default" else e["max"]
        print(f"| {state} | {timeout} | {pct} | {s['ejections_enforced_total']} | {s['ejections_overflow']} |")
    print()
    if "slow-34" in data:
        print(f"p99 of all requests, row {rows31.index('slow-34') + 1} (ms): {data['slow-34']['p99_ms']}")
        print()
    rows = [c for c in ("slow-client-200ms", "slow-body-200ms") if c in data]
    if rows:
        print("Failed requests as k6 and the caller's sidecar access log saw them (`DC`: the client closed first, `UT`: route timeout):")
        print()
        print("| Row | k6 | count | access log | count |")
        print("|---|---|---:|---|---:|")
        for c in rows:
            k6, k6_n, log, log_n = failed(data[c])
            print(f"| {rows31.index(c) + 1} | {k6} | {k6_n} | {log} | {log_n} |")
        print()

rows32 = [c for c in ("route-200ms", "route-1000ms") if c in data]
if rows32:
    print("## 3-2. Route timeout (job `3-2`)")
    print()
    print(f"One slow Pod, `maxEjectionPercent` 34, load started {params['QUIET']} s after the Pods were ready. "
          f"Ejected Pod: OUTLIER CHECK `FAILED` at {params['MID']} s. p99 after ejection: requests started after the last request to the ejected Pod.")
    print()
    print("| Route timeout (ms) | Ejected Pod | 504 to the slow Pod | 504 to normal Pods | p99 after ejection (ms) |")
    print("|---:|---|---:|---:|---:|")
    for c in rows32:
        m = data[c]
        ejected = ", ".join(ROLE.get(r, r) for r in m["ejected_roles"]) or "none"
        to = {role: sum(n for code, n in codes.items() if code.startswith("504")) for role, codes in m["served"].items()}
        p = m["p99_while_ejected_ms"]
        print(f"| {ms(m['env']['route_timeout'])} | {ejected} | {to.get('bad', 0)} | {to.get('normal', 0)} | {p if p is not None else '-'} |")
    print()

if "route-200ms" in data:
    d = results / "route-200ms"
    slow_ip = next(p["status"]["podIP"] for p in json.loads((d / "pods.json").read_text())["items"] if p["metadata"]["labels"]["role"] == "bad")
    print(f"`istioctl proxy-config endpoint`, 200 ms row, at {params['MID']} s (`{slow_ip}` is the slow Pod):")
    print()
    print("```")
    print((d / "endpoint-mid.txt").read_text().rstrip())
    print("```")
