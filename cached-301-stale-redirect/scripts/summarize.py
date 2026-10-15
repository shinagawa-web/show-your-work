#!/usr/bin/env python3
import json
import sys

d = json.load(open(sys.argv[1]))
env, t = d["env"], d["timings"]

print("# Summary\n")
print(f"- run: {env['runId']}")
print(f"- playwright {env['playwright']}, node {env['node']}, {env['nginx']}")
for b, v in env["browsers"].items():
    ch = f" (channel {v['channel']})" if v["channel"] else ""
    print(f"- {b} {v['version']}{ch}")
print(f"- nproc {env['nproc']}, mem {env['totalMemMB']} MB, kernel {env['kernel']}")
print(f"- server ready to first opens done {(t['firstDone'] - t['serverReady']) / 1000:.1f} s, switch {(t['switchDone'] - t['switchStart']) / 1000:.1f} s, total {(t['end'] - t['start']) / 1000:.1f} s")
print(f"- valid: {d['valid']}")
for i in d["invalid"]:
    print(f"  - {i}")
print()

STEPS = ["first", "before-v4-page", "v4-page", "immediate", "after-wait", "restart"]
browsers = list(env["browsers"].keys())
conds = [c["id"] for c in d["conditions"]]
by = {}
for o in d["opens"]:
    by[(o["condition"], o["browser"], o["step"])] = o


def cell(o):
    if o is None:
        return ""
    if o.get("error"):
        return "ERROR"
    s = o.get("version") or "-"
    if o["step"] == "v4-page":
        csd = (o.get("headers") or {}).get("clear-site-data")
        if o["browser"] == "chromium":
            ids = {e["requestId"] for e in o.get("cdp", []) if e["event"] == "requestWillBeSent" and e["url"] == o["url"]}
            seen = [e["headers"] for e in o.get("cdp", []) if e["event"] == "responseReceivedExtraInfo" and e["requestId"] in ids]
            csd = next((h.get("Clear-Site-Data") or h.get("clear-site-data") for h in seen if h.get("Clear-Site-Data") or h.get("clear-site-data")), None)
        return f"{s} csd={csd}"
    s += f" nginx={'yes' if o.get('reachedNginx') else 'no'}"
    if o["browser"] == "chromium":
        fdc = o.get("entranceFromDiskCache")
        s += f" disk={'-' if fdc is None else ('yes' if fdc else 'no')}"
    if o.get("secSinceSwitch") is not None and o["step"] != "first":
        s += f" @{o['secSinceSwitch']:.0f}s"
    return s


print("## Opens\n")
print("Cell: version reached, whether the entrance request reached nginx, Chromium `fromDiskCache` of the entrance redirect, seconds since the switch to v4.\n")
print("| condition | browser | " + " | ".join(STEPS) + " |")
print("|---|---|" + "---|" * len(STEPS))
for c in conds:
    for b in browsers:
        print(f"| {c} | {b} | " + " | ".join(cell(by.get((c, b, s))) for s in STEPS) + " |")
print()

print("## Responses from nginx (curl), before and after the switch to v4\n")
for phase in ["v3", "v4"]:
    for url, v in d["curl"][phase].items():
        raw = v["raw"].strip()
        print(f"### {phase} {url}\n")
        print("```")
        print(raw)
        print("```\n")
