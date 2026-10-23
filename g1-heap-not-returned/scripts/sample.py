#!/usr/bin/env python3
import os
import sys
import time

cg, out, t0 = sys.argv[1], sys.argv[2], float(sys.argv[3])


def read(name):
    with open(os.path.join(cg, name)) as f:
        return f.read()


def kv(text):
    return dict(line.split() for line in text.splitlines() if line.strip())


with open(out, "w") as f:
    f.write("t_s\tmemory_current_mb\tanon_mb\n")
    nxt = time.time()
    while True:
        try:
            cur = int(read("memory.current"))
            stat = kv(read("memory.stat"))
        except OSError:
            break
        f.write("%.1f\t%.1f\t%.1f\n" % (
            time.time() - t0, cur / 1048576, int(stat["anon"]) / 1048576))
        f.flush()
        nxt += 1
        time.sleep(max(0, nxt - time.time()))
