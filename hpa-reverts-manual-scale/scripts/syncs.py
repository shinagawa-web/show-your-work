import re
import sys

names = sys.argv[1:]
line_re = re.compile(r"^[IWE]\d{4} (\d\d:\d\d:\d\d\.\d{3})\d* +\d+ \S+\] (.*)$")
for raw in sys.stdin:
    m = line_re.match(raw.rstrip("\n"))
    if not m:
        continue
    t, msg = m.groups()
    target = re.search(r'(?:scaleTarget="Deployment/default/|HPA="default/|for Deployment/default/)([\w-]+)', msg)
    if not target or target.group(1) not in names:
        continue
    name = target.group(1)
    kv = dict(re.findall(r'(\w+)=("[^"]*"|\S+)', msg))
    if msg.startswith('"Proposing desired replicas"'):
        print(f"{t} {name} proposing desired={kv['desiredReplicas']} metricTimestamp={kv.get('timestamp')}")
    elif msg.startswith('"Decided not to scale"'):
        print(f"{t} {name} decided desired={kv['desiredReplicas']}")
    elif msg.startswith('"Successfully rescaled"'):
        print(f"{t} {name} rescaled current={kv['currentReplicas']} desired={kv['desiredReplicas']} reason={kv['reason']}")
    elif "failed to compute desired number of replicas" in msg or "failed to rescale" in msg:
        print(f"{t} {name} error {msg}")
