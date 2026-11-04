import json
import os
import subprocess
import sys
import time

results, node, op = sys.argv[1], sys.argv[2], float(sys.argv[3])
max_obs = int(os.environ["MAX_OBS"])
quiet_reads = int(os.environ["QUIET_READS"])
quiet_seconds = int(os.environ["QUIET_SECONDS"])
quiet_db_cpu = float(os.environ["QUIET_DB_CPU"])
path = os.path.join(results, "cpu.jsonl")


def ncpus(spec):
    n = 0
    for part in spec.split(","):
        a, _, b = part.partition("-")
        n += int(b or a) - int(a) + 1
    return n


pg_cpus = ncpus(os.environ["PG_CPUS"])


def stats():
    now = time.time()
    out = subprocess.run(["docker", "exec", node, "sh", "-c", "tail -qn1 /stats/*.jsonl"],
                         capture_output=True, text=True).stdout
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    live = [r for r in rows if r["t"] >= now - 3]
    return sum(r["db_start"] for r in rows), sum(r["db_inflight"] for r in live), len(live)


def samples():
    rows = []
    for line in open(path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if "pg_usec" in r:
            rows.append((r["t"], r["pg_usec"]))
    return rows


def percent(a, b):
    return (b[1] - a[1]) / (b[0] - a[0]) / 1e4 / pg_cpus


before = [r for r in samples() if op - 10 <= r[0] < op]
base = percent(before[0], before[-1])
prev = stats()[0]
streak = None
nxt = time.time() + 1
while True:
    time.sleep(max(0, nxt - time.time()))
    nxt += 1
    total, inflight, live = stats()
    rows = samples()
    now = time.time()
    quiet = live > 0 and total - prev < quiet_reads and inflight == 0 and percent(rows[-2], rows[-1]) <= base + quiet_db_cpu
    prev = total
    streak = (streak or now) if quiet else None
    if streak and now - streak >= quiet_seconds - 1:
        print("recovered %.3f" % streak)
        print("stop %.3f recovered" % now)
        break
    if now >= op + max_obs:
        print("stop %.3f max_%ds_after_operation" % (now, max_obs))
        break
