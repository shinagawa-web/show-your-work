import json
import subprocess
import sys
import time

node, op, max_obs, quiet_reads, quiet_seconds = sys.argv[1], float(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])


def total():
    out = subprocess.run(["docker", "exec", node, "sh", "-c", "tail -qn1 /stats/*.jsonl"],
                         capture_output=True, text=True).stdout
    return sum(json.loads(line)["db_start"] for line in out.splitlines() if line.strip())


prev = total()
quiet = 0
nxt = time.time() + 1
while True:
    time.sleep(max(0, nxt - time.time()))
    nxt += 1
    cur = total()
    quiet = quiet + 1 if cur - prev < quiet_reads else 0
    prev = cur
    now = time.time()
    if quiet >= quiet_seconds:
        print("stop %.3f db_reads_below_%d_for_%ds" % (now, quiet_reads, quiet_seconds))
        break
    if now >= op + max_obs:
        print("stop %.3f max_%ds_after_operation" % (now, max_obs))
        break
