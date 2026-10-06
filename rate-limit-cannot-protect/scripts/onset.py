#!/usr/bin/env python3
"""Hypothesis B: ordering around the switch at 100 ms resolution.

Columns: kernel recvq / inuse / limit (100 ms samples), bpftrace accept-wait p99 of
accepts in the bucket, nginx in-flight at the bucket start, k6 p99 by request start
and by completion, non-200 by completion. Then the first time each crosses a threshold.
"""
import math
import sys
from collections import defaultdict

from runload import Run, pct

d, sw = sys.argv[1], float(sys.argv[2])
R = Run(d)

pre_k6 = pct([x[1] for x in R.k6 if 0 <= x[0] < sw], 99)
pre_wait = pct([v for t, v in R.acc if 0 <= t < sw], 99)
limit0 = int(R.kern_before(sw - 0.5)['limit'])
print(f'pre-switch [0,{sw:.0f})s k6 p99_ms={pre_k6:.1f} accept_wait p99_ms={pre_wait:.3f} limit={limit0}')

bs, be, ba = defaultdict(list), defaultdict(list), defaultdict(list)
for x in R.k6:
    bs[math.floor(x[0] * 10)].append(x)
    be[math.floor(x[5] * 10)].append(x)
for t, v in R.acc:
    ba[math.floor(t * 10)].append(v)
bk = {}
for r in R.kern:
    bk.setdefault(math.floor(r['t'] * 10), r)

print()
print('## 100 ms timeline')
hdr = ['t', 'recvq', 'inuse', 'limit', 'acc_n', 'wait_p99', 'ngx_conc', 'start_p99', 'end_p99', 'end_non200']
print(' '.join(f'{h:>10}' for h in hdr))
for b in range(int((sw - 1) * 10), int((sw + 10) * 10)):
    r = bk.get(b)
    row = [f'{b / 10:.1f}', '' if r is None else int(r['recvq_total']), '' if r is None else int(r['inuse']),
           '' if r is None else int(r['limit']), len(ba.get(b, [])), f'{pct(ba.get(b, []), 99):.2f}',
           R.ngx_conc(b / 10), f'{pct([x[1] for x in bs.get(b, [])], 99):.1f}',
           f'{pct([x[1] for x in be.get(b, [])], 99):.1f}', sum(1 for x in be.get(b, []) if x[2] != '200')]
    print(' '.join(f'{str(v):>10}' for v in row))


def first(label, rows, pred):
    for t, v in rows:
        if t >= sw and pred(v):
            print(f'{label}: t={t:.2f}s (value={v})')
            return
    print(f'{label}: not reached')


print()
print('## first crossing after the switch')
kr = [(r['t'], r) for r in R.kern]
first('kernel recvq > 0 (100 ms sample)', [(t, int(r['recvq_total'])) for t, r in kr], lambda v: v > 0)
first('kernel inuse >= limit (100 ms sample)', [(t, (int(r['inuse']), int(r['limit']))) for t, r in kr], lambda v: v[0] >= v[1])
wr = [(b / 10, pct(ba[b], 99)) for b in sorted(ba)]
for thr in (max(2 * pre_wait, 1.0), 10.0, 100.0):
    first(f'bpf accept wait p99 per 100 ms (by accept time) > {thr:.3f} ms', wr, lambda v, thr=thr: v > thr)
first(f'nginx in-flight (100 ms instants) > limit {limit0}', [(i / 10, R.ngx_conc(i / 10)) for i in range(int(sw * 10), int((sw + 20) * 10))],
      lambda v: v > limit0)
sr = [(b / 10, pct([x[1] for x in bs[b]], 99)) for b in sorted(bs)]
er = [(b / 10, pct([x[1] for x in be[b]], 99)) for b in sorted(be)]
for thr in (2 * pre_k6, 500, 1000):
    first(f'k6 p99 per 100 ms (by start) > {thr:.1f} ms', sr, lambda v, thr=thr: v > thr)
    first(f'k6 p99 per 100 ms (by completion) > {thr:.1f} ms', er, lambda v, thr=thr: v > thr)
first('first non-200 response (by completion)', [(b / 10, sum(1 for x in be[b] if x[2] != '200')) for b in sorted(be)],
      lambda v: v > 0)
