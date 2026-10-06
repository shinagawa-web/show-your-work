#!/usr/bin/env python3
"""runs.txt: the repeated runs of one scenario (results/<scenario>/run-<i>/) side by side.

Window [SWITCH_AT, DURATION) of each run: k6 status counts and p99, accept queue max,
connections per nginx entry. For tenant scenarios, nginx 429 per tenant (by request start).
For runs with onset.txt, the "first crossing after the switch" lines of each run.
"""
import glob
import os
import sys
from collections import Counter

from runload import Run, pct

d = sys.argv[1].rstrip('/')
runs = sorted(glob.glob(f'{d}/run-*'), key=lambda p: int(p.rsplit('-', 1)[1]))
print(f'# {os.path.basename(d)}: {len(runs)} runs')

for r in runs:
    meta = dict(l.strip().split('=', 1) for l in open(f'{r}/meta.env') if '=' in l)
    sw, dur = int(meta['SWITCH_AT']), int(meta['DURATION'])
    R = Run(r)
    ks = [k for k in R.k6 if sw <= k[0] < dur]
    st = Counter(k[2] for k in ks)
    ok = [k[1] for k in ks if k[2] == '200']
    ns = [x for x in R.ngx if sw <= x['start'] < dur]
    e0, e1 = R.est_before(sw), R.est_before(dur)
    same = sum(1 for x in R.ngx if e0[0] <= x['start'] < e1[0])
    w = [x for x in R.kern if sw <= x['t'] < dur]
    print()
    print(f'## {os.path.basename(r)} window [{sw}s, {dur}s)')
    print(f'k6: requests={len(ks)} status=' + ','.join(f'{s}:{c}' for s, c in sorted(st.items())) +
          f' error_pct(non-200)={100 * (len(ks) - st.get("200", 0)) / max(len(ks), 1):.2f}'
          f' p99_ms(all)={pct([k[1] for k in ks], 99):.1f} p99_ms(200 only)={pct(ok, 99):.1f}')
    print(f'kernel recvq max={max(x["recvq_total"] for x in w):.0f} mean={sum(x["recvq_total"] for x in w) / len(w):.1f}'
          f'  connections/nginx_entry={(e1[1] - e0[1]) / max(same, 1):.3f}')
    tenants = sorted(set(x['tenant'] for x in R.ngx if x['tenant'] not in ('none', '-')))
    if tenants:
        print('nginx per tenant (entries / 429):')
        print(' '.join(f'{h:>6}' for h in ['', *tenants, 'total']))
        ent = [sum(1 for x in ns if x['tenant'] == t) for t in tenants]
        rej = [sum(1 for x in ns if x['tenant'] == t and x['status'] == '429') for t in tenants]
        print(' '.join(f'{v:>6}' for v in ['entry', *ent, sum(ent)]))
        print(' '.join(f'{v:>6}' for v in ['429', *rej, sum(rej)]))
        pctl = [f'{100 * a / max(b, 1):.1f}' for a, b in zip(rej, ent)]
        print(' '.join(f'{v:>6}' for v in ['429%', *pctl, f'{100 * sum(rej) / max(sum(ent), 1):.1f}']))
        pre = [x for x in R.ngx if 0 <= x['start'] < sw]
        print(f'nginx 429 before the switch [0,{sw})s: {sum(1 for x in pre if x["status"] == "429")}'
              f' of {len(pre)}')
    if os.path.exists(f'{r}/onset.txt'):
        lines = open(f'{r}/onset.txt').read().split('## first crossing after the switch', 1)[1].strip().splitlines()
        print('onset.txt, first crossing after the switch:')
        for l in lines:
            print('  ' + l)
