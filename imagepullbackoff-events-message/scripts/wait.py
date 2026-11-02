import sys

import obs

ev_path, pod_path = sys.argv[1], sys.argv[2]
evs = obs.events(ev_path)
missing = []
for spec in sys.argv[3:]:
    parts = spec.split(":")
    if parts[0] == "count":
        _, pod, n = parts
        f = obs.pull_failures(evs, pod)
        c = max((e["count"] for e in f), default=0)
        if c < int(n):
            missing.append(f"{pod} pull failures {c}/{n}")
        continue
    pod, expect = parts
    st = obs.pod_states(pod_path, pod)
    if expect == "run" and not obs.running(st):
        missing.append(f"{pod} not running")
    elif expect == "fail" and not (obs.first_pull_failure(evs, pod) and obs.backoff_record(st)):
        missing.append(f"{pod} no pull failure and back-off yet")
    elif expect == "crash" and not obs.terminated(st):
        missing.append(f"{pod} not terminated yet")
print("; ".join(missing) or "complete")
sys.exit(1 if missing else 0)
