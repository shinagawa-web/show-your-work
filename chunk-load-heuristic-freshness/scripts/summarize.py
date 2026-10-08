#!/usr/bin/env python3
import json
import re
import statistics
import sys

d = json.load(open(sys.argv[1]))
env, t = d["env"], d["timings"]


def sec(a, b):
    return f"{(t[b] - t[a]) / 1000:.1f}" if a in t and b in t else "-"


print("# Summary\n")
print(f"- run: {env['runId']}")
print(f"- playwright {env['playwright']}, browser {env['browserVersion']}, node {env['node']}, {env['nginx']}")
print(f"- nproc {env['nproc']}, mem {env['totalMemMB']} MB, kernel {env['kernel']}")
print(f"- parallel groups {env['parallel']}, max browsers {env['maxBrowsers']}, peak browsers {d['peakBrowsers']}")
print(f"- calibration {sec('calibrationStart', 'calibrationEnd')} s, main {sec('mainStart', 'mainEnd')} s, total {sec('start', 'end')} s")
ls = [s for s in d["loadSamples"] if s["cpuUtil"] is not None]
if ls:
    print(f"- loadavg1 max {max(s['loadavg1'] for s in ls):.2f}, cpu util mean {statistics.mean(s['cpuUtil'] for s in ls):.2f} max {max(s['cpuUtil'] for s in ls):.2f}")
if d.get("stoppedAfterCalibration"):
    print("- STOPPED after calibration (a calibration user booted a version that contradicts its measured r/A)")
print()

STATUS = re.compile(r'"(?:GET|HEAD) (\S+) HTTP/[\d.]+" (\d{3}) (\d+)')
COND = re.compile(r'inm="([^"]*)" ims="([^"]*)"')


def log_summary(lines):
    out = []
    for l in lines:
        m = STATUS.search(l)
        if m:
            path = m.group(1)
            path = re.sub(r"^/assets/", "", path)
            c = COND.search(l)
            cond = f" [inm={c.group(1)} ims={c.group(2)}]" if c else ""
            out.append(f"{path} {m.group(2)}{cond}")
    return "; ".join(out) or "-"


def chunk_summary(u):
    parts = []
    for a in u["chunk"] or []:
        r = a.get("result") or {}
        if r.get("loaded"):
            s = f"ok({r['loaded']})"
        elif r.get("errors"):
            s = "ERR " + r["errors"][0]
        else:
            s = "none"
        if a.get("reloaded"):
            av = (a.get("afterReload") or {}).get("app") or {}
            ev = [e for e in av.get("log", []) if e["event"] in ("vite:preloadError", "chunk-error")]
            if ev:
                s = f"{ev[0]['event']}: {ev[0]['message']}"
            s += f" -> reloaded boots {av.get('version')}"
        parts.append(s)
    return " / ".join(parts)


def deploy_col(u):
    m = u["measured"]
    if u.get("deployAt") == "none":
        return "none"
    s = "y" if m["deployDoneBeforeRevisit"] else "N"
    if m.get("deployStartFrac") is not None:
        s += f" {u.get('deployAt', 'early')} {m['deployStartFrac']:.2f}-{m['deployDoneFrac']:.2f}"
    return s


def res_summary(rs):
    return "; ".join(f"{r['name'].rsplit('/', 1)[-1]} {r['transferSize']}/{r['responseStatus']}" for r in rs) or "-"


def first_visit(u):
    fv = u.get("firstVisitChunk")
    if not fv:
        return "-"
    parts = []
    for a in fv["result"] or []:
        r = a.get("result") or {}
        parts.append(f"ok({r['loaded']})" if r.get("loaded") else ("ERR " + r["errors"][0] if r.get("errors") else "none"))
    return f"{' / '.join(parts)} [{res_summary(fv['resources'])}]"


def row(u):
    m = u["measured"]
    rv = u["revisit"]
    booted = u["bootedVersion"]
    if u["method"] == "reload":
        ar = rv.get("afterReload") or {}
        booted = f"{booted} -> reload {((ar.get('state') or {}).get('app') or {}).get('version')}"
        ts = f"{u['navTransferSize']} -> {((ar.get('state') or {}).get('nav') or {}).get('transferSize')}"
    else:
        ts = str(u["navTransferSize"])
    fh = u["fetch"]["response"] or {}
    return (
        f"| {u['user']} | {m['A_sec']:.0f} | {fh.get('age') or '-'} | {m['elapsedSec']:.1f} | {m['r_sec']:.1f} | "
        f"{m['r_over_A']:.4f} | {deploy_col(u)} | {booted} | {ts} | "
        f"{chunk_summary(u)} | {log_summary(u['accessLog']['v1'])} | {log_summary(u['accessLog']['v2'])} |"
    ) + (f" {first_visit(u)} | {res_summary(u.get('revisitChunkResources') or [])} |" if u["set"] == "pressfirst" else "")


users = d["users"]
groups = {}
for u in users:
    groups.setdefault(u["set"], []).append(u)

for s, us in groups.items():
    print(f"## {s}\n")
    extra = s == "pressfirst"
    print("| user | A s | Age hdr | elapsed s | r s | r/A | deploy (before revisit, timing, start-done as fraction of fetch->revisit) | booted | nav transferSize | chunk | v1 log (fetch) | v2 log (revisit) |"
          + (" first-visit chunk [file transferSize/status] | revisit chunk file transferSize/status |" if extra else ""))
    print("|---|---|---|---|---|---|---|---|---|---|---|---|" + ("---|---|" if extra else ""))
    for u in sorted(us, key=lambda u: (u["target"]["ageSec"], u["measured"]["r_over_A"])):
        print(row(u))
    print()

print("## Boundary\n")
print("| set | A s (target) | method | deploy | max r/A booting v1 | min r/A booting v2 | n |")
print("|---|---|---|---|---|---|---|")
bnd = {}
for u in users:
    k = (u["set"], u["target"]["ageSec"], u["method"], u.get("deployAt", "early"))
    bnd.setdefault(k, []).append(u)
for (s, a, meth, dep), us in sorted(bnd.items()):
    v1 = [u["measured"]["r_over_A"] for u in us if u["bootedVersion"] == "v1"]
    v2 = [u["measured"]["r_over_A"] for u in us if u["bootedVersion"] == "v2"]
    print(f"| {s} | {a} | {meth} | {dep} | {max(v1):.4f} | " if v1 else f"| {s} | {a} | {meth} | {dep} | - | ", end="")
    print(f"{min(v2):.4f} | {len(us)} |" if v2 else f"- | {len(us)} |")
print()

if d.get("calibration"):
    c = d["calibration"]
    print("## Calibration (judged by measured r/A)\n")
    print(f"- rule: {c['rule']}")
    print(f"- result: {'pass' if c['pass'] else 'FAIL'}\n")
    print("| user | method | A s | r s | r/A | expected | booted | result |")
    print("|---|---|---|---|---|---|---|---|")
    for r in c["rows"]:
        a = f"{r['A_sec']:.0f}" if r["A_sec"] is not None else "-"
        rs = f"{r['r_sec']:.1f}" if r["r_sec"] is not None else "-"
        ra = f"{r['r_over_A']:.4f}" if r["r_over_A"] is not None else "-"
        print(f"| {r['user']} | {r['method']} | {a} | {rs} | {ra} | {r['expected'] or '-'} | {r['booted'] or '-'} | {'pass' if r['pass'] else 'FAIL'} |")
