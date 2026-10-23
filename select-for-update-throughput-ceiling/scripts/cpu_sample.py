#!/usr/bin/env python3
import sys
import time

path = sys.argv[1]
interval = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
print("time_ns\tusage_usec", flush=True)
while True:
    with open(path) as f:
        usage = next(int(line.split()[1]) for line in f if line.startswith("usage_usec "))
    print(f"{time.time_ns()}\t{usage}", flush=True)
    time.sleep(interval)
