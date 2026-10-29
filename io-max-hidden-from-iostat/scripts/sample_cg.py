import sys
import time

cg, majmin, n = sys.argv[1], sys.argv[2], int(sys.argv[3])


def rios():
    with open(f"{cg}/io.stat") as f:
        for line in f:
            parts = line.split()
            if parts and parts[0] == majmin:
                return dict(p.split("=") for p in parts[1:]).get("rios", "0")
    return "0"


def pressure():
    out = {}
    with open(f"{cg}/io.pressure") as f:
        for line in f:
            parts = line.split()
            out[parts[0]] = dict(p.split("=") for p in parts[1:])["total"]
    return out


print("t_s\trios\tsome_total_us\tfull_total_us")
t0 = time.monotonic()
for i in range(n + 1):
    p = pressure()
    print(f"{time.monotonic() - t0:.3f}\t{rios()}\t{p.get('some', '')}\t{p.get('full', '')}", flush=True)
    if i < n:
        time.sleep(max(0.0, t0 + i + 1 - time.monotonic()))
