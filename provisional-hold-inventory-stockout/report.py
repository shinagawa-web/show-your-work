#!/usr/bin/env python3
"""
Average the run.py tables from several outputs of `./run_all.sh run`.

  python3 report.py results-run-*.txt

Every output must come from the same number of rounds, so the mean of the
per-output means is the mean over all rounds. Each output prints its means
to one decimal, so the result can be off by up to 0.05.
"""
import collections
import sys

COLS = ["confirmed", "exp_during_co", "lost_sale", "dead_ratio"]

sums = collections.defaultdict(lambda: [0.0] * len(COLS))
counts = collections.Counter()
header = None
for path in sys.argv[1:]:
    in_run = False
    for line in open(path):
        if line.startswith("=== run.py"):
            in_run = True
            continue
        if line.startswith("==="):
            in_run = False
        if not in_run:
            continue
        if line.startswith("customers="):
            header = line.strip()
            continue
        parts = line.split()
        if len(parts) == 5 and parts[0].isdigit():
            T = int(parts[0])
            for i, v in enumerate(parts[1:]):
                sums[T][i] += float(v)
            counts[T] += 1

if not counts:
    sys.exit("no run.py table found")
n = set(counts.values())
if len(n) != 1:
    sys.exit(f"outputs disagree on the T values: {dict(counts)}")
n = n.pop()

print(f"{header} outputs={n}")
print(f"{'T(s)':>6}  {'confirmed':>10} {'exp_during_co':>14} {'lost_sale':>10} {'dead_ratio':>10}")
for T in sorted(sums):
    c, e, l, d = (v / n for v in sums[T])
    print(f"{T:>6}  {c:>10.1f} {e:>14.1f} {l:>10.1f} {d:>10.3f}")
