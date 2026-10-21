#!/usr/bin/env python3
import collections
import pathlib
import re
import struct
import sys

d = pathlib.Path(sys.argv[1])
invalid = []

MESSAGES = [
    ("prematurely closed", "upstream prematurely closed connection"),
    ("104", "recv() failed (104: Connection reset by peer)"),
    ("temporarily disabled", "upstream server temporarily disabled"),
    ("no live upstreams", "no live upstreams"),
]


def read(name):
    p = d / name
    return p.read_text().splitlines() if p.exists() else []


def settings():
    for ln in read("settings.txt"):
        print(ln)


def client():
    rows = [ln.split() for ln in read("client.log")]
    if not rows:
        invalid.append("the client recorded no request")
    by = collections.Counter()
    for t0, t1, kind, rid, method, status, err in rows:
        by[(method, status, err)] += 1
    print("## Client")
    for method in ("GET", "POST"):
        n = sum(v for (m, _, _), v in by.items() if m == method)
        c502 = sum(v for (m, s, _), v in by.items() if m == method and s == "502")
        other = {f"{s}/{e}": v for (m, s, e), v in sorted(by.items()) if m == method and s not in ("200", "502")}
        print(f"{method}: requests {n}, 502 {c502}, other than 200/502 {other or 0}")
    return {r[3]: r for r in rows}


def error_log():
    lines = read("error.log")
    counts = collections.Counter()
    failed, other = [], collections.Counter()
    for ln in lines:
        key = next((k for k, m in MESSAGES if m in ln), None)
        if key is None:
            other[re.sub(r"^\S+ \S+ \[(\w+)\] \d+#\d+: (\*\d+ )?", r"[\1] ", ln).split(",")[0]] += 1
            continue
        counts[key] += 1
        m = re.search(r'request: "(\w+) /\?id=(\d+) ', ln)
        u = re.search(r'upstream: "http://127\.0\.0\.1:(\d+)', ln)
        rec = (key, m.group(1) if m else "?", m.group(2) if m else "?", u.group(1) if u else "?")
        if key in ("prematurely closed", "104"):
            failed.append(rec)
    print("## nginx error log")
    for k, m in MESSAGES:
        print(f"{m}: {counts[k]}")
    for k, v in other.most_common():
        print(f"other: {k}: {v}")
    return failed


def backend():
    got = set()
    n = 0
    for ln in read("backend-requests.log"):
        g = ln.split()
        m = re.search(r"id=(\d+)", g[3])
        if m:
            got.add((m.group(1), g[1]))
            n += 1
    if n == 0:
        invalid.append("the backends received no request")
    return got


def pcap():
    p = d / "capture.pcap"
    if not p.exists() or p.stat().st_size <= 24:
        invalid.append("no packet capture")
        return {}, {}
    b = p.read_bytes()
    magic = struct.unpack("<I", b[:4])[0]
    scale = 1e-9 if magic == 0xA1B23C4D else 1e-6
    i = 24
    conns = collections.defaultdict(list)
    req_at = {}
    while i + 16 <= len(b):
        ts, frac, incl, _ = struct.unpack("<IIII", b[i:i + 16])
        pkt = b[i + 16:i + 16 + incl]
        i += 16 + incl
        t = ts + frac * scale
        if len(pkt) < 34 or pkt[12:14] != b"\x08\x00":
            continue
        ihl = (pkt[14] & 15) * 4
        tcp = pkt[14 + ihl:]
        sport, dport = struct.unpack(">HH", tcp[:4])
        flags = tcp[13]
        payload = tcp[(tcp[12] >> 4) * 4:]
        if dport in (8081, 8082):
            key, direction = (sport, dport), "req"
        else:
            key, direction = (dport, sport), "resp"
        f = "".join(c for c, bit in (("S", 2), ("F", 1), ("R", 4), ("P", 8)) if flags & bit)
        conns[key].append((t, direction, f))
        m = re.match(rb"(\w+) /\?id=(\d+) ", payload)
        if m and direction == "req":
            req_at[(m.group(2).decode(), str(key[1]))] = (key, t)
    return conns, req_at


def explain(failed, conns, req_at, got):
    print("## Failed attempts (prematurely closed, 104)")
    print(f"count: {len(failed)}")
    print(f"reached the backend: {sum(1 for _, _, rid, port in failed if (rid, port) in got)}")
    by = collections.Counter((k, m) for k, m, _, _ in failed)
    for (k, m), v in sorted(by.items()):
        print(f"{k} {m}: {v}")
    rows = collections.Counter()
    idles = collections.defaultdict(list)
    margins = collections.defaultdict(list)
    for key, method, rid, port in failed:
        hit = req_at.get((rid, port))
        if not hit:
            rows[(key, "request packet not in capture")] += 1
            continue
        conn, t = hit
        ev = conns[conn]
        before = [e for e in ev if e[0] < t]
        syn = max((e[0] for e in before if e[1] == "req" and "S" in e[2]), default=None)
        prev = [e[0] for e in before if e[1] == "req" and "P" in e[2] and (syn is None or e[0] > syn)]
        reused = "reused" if prev else "new connection"
        nxt = min((e[0] for e in ev if e[0] > t and "S" in e[2]), default=float("inf"))
        after = [e for e in ev if e[1] == "resp" and t - 0.05 <= e[0] < nxt and ("F" in e[2] or "R" in e[2])]
        fin = next((e[0] for e in after if "F" in e[2]), None)
        rst = next((e[0] for e in after if "R" in e[2]), None)
        if fin is not None:
            closed_at = fin
            seq = "backend FIN" + (" then RST" if rst else "")
        elif rst is not None:
            closed_at = rst
            seq = "backend RST without FIN"
        else:
            rows[(key, f"{reused}, no FIN/RST from backend")] += 1
            continue
        when = "arrived after the backend closed" if t > closed_at else "arrived before the backend closed"
        rows[(key, f"{reused}, {seq}, request {when}")] += 1
        margins[key].append((t - closed_at) * 1e6)
        if prev:
            idles[key].append(t - prev[-1])
    for (k, what), v in sorted(rows.items()):
        print(f"{k}: {what}: {v}")
    for k, v in sorted(margins.items()):
        v.sort()
        print(f"{k}: request arrival minus backend close, us: {' '.join(f'{x:.0f}' for x in v)}")
    for k, v in sorted(idles.items()):
        v.sort()
        print(f"{k}: idle time of the reused connection, s: min {v[0]:.3f} median {v[len(v) // 2]:.3f} max {v[-1]:.3f}")


def connections(conns):
    opened = closed_by_backend = closed_by_nginx = 0
    for ev in conns.values():
        opened += sum(1 for e in ev if e[1] == "req" and "S" in e[2])
        first = None
        for t, direction, f in ev:
            if "S" in f:
                first = None
            elif ("F" in f or "R" in f) and first is None:
                first = direction
                if direction == "resp":
                    closed_by_backend += 1
                else:
                    closed_by_nginx += 1
    print("## Connections nginx opened to the backends")
    print(f"opened: {opened}, first FIN/RST from the backend: {closed_by_backend}, from nginx: {closed_by_nginx}")


def get_retries(failed, cl):
    gets = [(rid, port) for k, m, rid, port in failed if m == "GET"]
    if not gets:
        return
    final = collections.Counter(cl[rid][5] if rid in cl else "?" for rid, _ in gets)
    print(f"GET failed attempts, client status: {dict(final)}")


def bursts(failed, req_at):
    rows = [ln.split() for ln in read("bursts.log")]
    if not rows:
        return
    gaps = sorted(float(g) for _, g in rows if g != "-")
    print("## Bursts")
    print(f"bursts: {len(rows)}, interval s: min {gaps[0]:.3f} max {gaps[-1]:.3f}" if gaps else f"bursts: {len(rows)}")
    times = [req_at[(rid, port)][1] for _, _, rid, port in failed if (rid, port) in req_at]
    hit = []
    for t, g in rows:
        n = sum(1 for x in times if float(t) <= x < float(t) + 0.5)
        if n:
            hit.append(f"{g}s:{n}")
    print(f"bursts followed by failed attempts within 0.5s (interval:failed): {' '.join(hit) or 'none'}")


def checks():
    t = (d / "tcpdump.log").read_text() if (d / "tcpdump.log").exists() else ""
    m = re.search(r"(\d+) packets dropped by kernel", t)
    print(f"capture: {m.group(0) if m else 'no drop count'}")
    if not m or m.group(1) != "0":
        invalid.append("the packet capture is incomplete")
    if d.name.startswith("idle-close") and len(read("bursts.log")) < 15:
        invalid.append("fewer than 15 bursts")


settings()
cl = client()
failed = error_log()
got = backend()
conns, req_at = pcap()
explain(failed, conns, req_at, got)
connections(conns)
get_retries(failed, cl)
bursts(failed, req_at)
checks()
for x in invalid:
    print(f"RUN INVALID: {x}")
sys.exit(1 if invalid else 0)
