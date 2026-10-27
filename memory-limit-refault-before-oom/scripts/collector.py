import json
import os
import sys
import time


def read(p):
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def kv(text):
    out = {}
    for line in (text or "").splitlines():
        k, _, v = line.partition(" ")
        if v.strip().lstrip("-").isdigit():
            out[k] = int(v)
    return out


def psi(text):
    out = {}
    for line in (text or "").splitlines():
        kind, *fields = line.split()
        for f in fields:
            k, _, v = f.partition("=")
            if k == "total":
                out[kind] = int(v)
    return out


def num(text):
    if text is None:
        return None
    text = text.strip()
    return text if not text.lstrip("-").isdigit() else int(text)


def sample(cg, parent):
    cur = read(os.path.join(cg, "memory.current"))
    s = {"t": time.time(), "alive": cur is not None}
    if cur is not None:
        s["current"] = num(cur)
        s["max"] = num(read(os.path.join(cg, "memory.max")))
        s["stat"] = kv(read(os.path.join(cg, "memory.stat")))
        s["events"] = kv(read(os.path.join(cg, "memory.events")))
        s["mem_psi"] = psi(read(os.path.join(cg, "memory.pressure")))
        s["io_psi"] = psi(read(os.path.join(cg, "io.pressure")))
        s["cpu_psi"] = psi(read(os.path.join(cg, "cpu.pressure")))
        s["cpu"] = kv(read(os.path.join(cg, "cpu.stat")))
        s["swap_max"] = num(read(os.path.join(cg, "memory.swap.max")))
        s["swap_current"] = num(read(os.path.join(cg, "memory.swap.current")))
    s["parent_events"] = kv(read(os.path.join(parent, "memory.events")))
    return s


def main():
    cg, parent, interval, duration = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
    t0 = time.monotonic()
    k = 0
    while True:
        print(json.dumps(sample(cg, parent)), flush=True)
        k += 1
        nxt = t0 + k * interval
        if nxt - t0 > duration:
            break
        time.sleep(max(0.0, nxt - time.monotonic()))


main()
