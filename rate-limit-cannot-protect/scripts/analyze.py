#!/usr/bin/env python3
"""summary.txt of one scenario from k6.csv, access.log, kernel_100ms.csv and bpf.log.

k6 and nginx rows are bucketed by request start time. Kernel values in the per-second
table are the first 100 ms sample in that second. bpftrace accept waits are bucketed by
accept time, accept-to-close times by close time.

Per-endpoint accept-to-close: bpftrace does not see the URI, so each connection seen by
bpftrace (established at E, closed at C) is matched to a single-attempt nginx access log
line whose start ($msec - $request_time) lies in [E - 3 ms, E + 1 ms]; for a 200 line its
end ($msec) must also lie in [C - 1 ms, C + 2 ms] (nginx times have 1 ms resolution; for a
504 nginx gave up before the downstream answered, so only the start is used). Only pairs
where the connection has exactly one candidate line and that line exactly one candidate
connection are used; the counts are printed.
"""
import bisect
import math
import sys
from collections import Counter, defaultdict

from runload import Run, pct

d = sys.argv[1]
SW, DUR = int(sys.argv[2]), int(sys.argv[3])
R = Run(d)
T = max(int(max(x[0] for x in R.k6)) + 1, DUR)

print(f'# {d.rstrip("/").split("/")[-1]}')
print(f'k6 requests: {len(R.k6)}  nginx log lines: {len(R.ngx)}  kernel samples: {len(R.kern)}'
      f'  bpf accepts: {len(R.acc)}  bpf closes: {len(R.close)}')
print()
print('## per second')
print('ngx_conc = nginx requests in flight at the start of the second (from $msec - $request_time and $msec)')
print('recvq = accept queue (Recv-Q of LISTEN :8081), inuse = accepted and not closed, util = inuse / limit')
print('acc_n / wait = accepts in that second and their accept wait (ms); svc = accept-to-close (ms) of closes in that second')
print('heavy% = share of /api/heavy among nginx entries in that second')
hdr = ['t', 'k6_req', 'k6_p50', 'k6_p99', 'k6_non200', 'ngx_entry', 'heavy%', 'ngx_429', 'ngx_5xx', 'ngx_att', 'ngx_conc',
       'est/s', 'recvq', 'inuse', 'limit', 'util_pct', 'ovf', 'acc_n', 'wait_p50', 'wait_p99', 'svc_p50', 'svc_p99']
print(' '.join(f'{h:>9}' for h in hdr))
bk = defaultdict(list)
for x in R.k6:
    bk[int(x[0])].append(x)
bn = defaultdict(list)
for x in R.ngx:
    bn[math.floor(x['start'])].append(x)
ba, bc = defaultdict(list), defaultdict(list)
for t, v in R.acc:
    ba[math.floor(t)].append(v)
for t, v, _ in R.close:
    bc[math.floor(t)].append(v)
prev_k = prev_e = None
for t in range(T):
    ks, ns, k = bk.get(t, []), bn.get(t, []), R.kern_at(t)
    e = R.est_before(t + 1)
    est = '' if prev_e is None else e[1] - prev_e[1]
    prev_e = e
    ovf = '' if (k is None or prev_k is None) else int(k['listen_overflows'] - prev_k['listen_overflows'])
    if k is not None:
        prev_k = k
    row = [t, len(ks), f'{pct([x[1] for x in ks], 50):.1f}', f'{pct([x[1] for x in ks], 99):.1f}',
           sum(1 for x in ks if x[2] != '200'), len(ns),
           f"{100 * sum(1 for n in ns if n['uri'].startswith('/api/heavy')) / len(ns):.1f}" if ns else '',
           sum(1 for n in ns if n['status'] == '429'),
           sum(1 for n in ns if n['status'].startswith('5')), sum(n['attempts'] for n in ns), R.ngx_conc(t), est,
           '' if k is None else int(k['recvq_total']), '' if k is None else int(k['inuse']),
           '' if k is None else int(k['limit']),
           '' if k is None or not k['limit'] else f"{100 * k['inuse'] / k['limit']:.1f}", ovf,
           len(ba.get(t, [])), f'{pct(ba.get(t, []), 50):.1f}', f'{pct(ba.get(t, []), 99):.1f}',
           f'{pct(bc.get(t, []), 50):.1f}', f'{pct(bc.get(t, []), 99):.1f}']
    print(' '.join(f'{str(v):>9}' for v in row))

tenants = sorted(set(x['tenant'] for x in R.ngx if x['tenant'] not in ('none', '-')))
if tenants:
    print()
    print('## nginx 429 per second per tenant (bucketed by request start)')
    print(' '.join(f'{h:>6}' for h in ['t'] + tenants + ['total']))
    for t in range(T):
        ns = bn.get(t, [])
        row = [t] + [sum(1 for n in ns if n['tenant'] == tn and n['status'] == '429') for tn in tenants]
        row.append(sum(row[1:]))
        print(' '.join(f'{str(v):>6}' for v in row))


# match accept-to-close events to nginx lines (see docstring)
_one = sorted((x['start'], i) for i, x in enumerate(R.ngx) if x['attempts'] == 1)
_one_t = [st for st, _ in _one]
_cand = []
_rev = Counter()
for t, v, e2c in R.close:
    if e2c is None:
        _cand.append([])
        continue
    est = t - e2c / 1000
    lo, hi = bisect.bisect_left(_one_t, est - 0.003), bisect.bisect_right(_one_t, est + 0.001)
    c = [_one[j][1] for j in range(lo, hi)]
    c = [i for i in c if R.ngx[i]['status'] != '200' or t - 0.001 <= R.ngx[i]['end'] <= t + 0.002]
    _cand.append(c)
    for i in c:
        _rev[i] += 1
MATCHED = []  # (close_t, accept_to_close_ms, uri, nginx status)
for (t, v, _), c in zip(R.close, _cand):
    if len(c) == 1 and _rev[c[0]] == 1:
        MATCHED.append((t, v, R.ngx[c[0]]['uri'], R.ngx[c[0]]['status']))


def stats(v):
    return f'mean={sum(v) / len(v):.1f} max={max(v):.1f} last={v[-1]:.1f}' if v else 'n/a'


def window(a, b):
    print()
    print(f'## window [{a}s, {b}s)')
    span = b - a
    ks = [k for k in R.k6 if a <= k[0] < b]
    n = len(ks)
    st = Counter(k[2] for k in ks)
    print(f'k6: requests={n} rps={n / span:.1f} p50_ms={pct([k[1] for k in ks], 50):.1f} p99_ms={pct([k[1] for k in ks], 99):.1f}'
          f' error_pct(non-200)={100 * (n - st.get("200", 0)) / max(n, 1):.2f}')
    for code in sorted(st):
        v = [k[1] for k in ks if k[2] == code]
        print(f'k6 status={code} only: requests={len(v)} p50_ms={pct(v, 50):.1f} p99_ms={pct(v, 99):.1f} max_ms={max(v):.1f}')
    print('k6 status: ' + ' '.join(f'{s}={c}({100 * c / max(n, 1):.2f}%)' for s, c in sorted(st.items())))
    for ep in sorted(set(k[3] for k in ks)):
        e = [k for k in ks if k[3] == ep]
        est = Counter(k[2] for k in e)
        print(f'k6 ep={ep}: requests={len(e)} rps={len(e) / span:.1f} p50_ms={pct([k[1] for k in e], 50):.1f}'
              f' p99_ms={pct([k[1] for k in e], 99):.1f} status=' + ','.join(f'{s}:{c}' for s, c in sorted(est.items())))
    tns = sorted(set(k[4] for k in ks))
    if tns != ['none']:
        for tn in tns:
            e = [k for k in ks if k[4] == tn]
            est = Counter(k[2] for k in e)
            print(f'k6 tenant={tn}: requests={len(e)} rps={len(e) / span:.1f} p50_ms={pct([k[1] for k in e], 50):.1f}'
                  f' p99_ms={pct([k[1] for k in e], 99):.1f} status=' + ','.join(f'{s}:{c}' for s, c in sorted(est.items())))
    ns = [x for x in R.ngx if a <= x['start'] < b]
    uc = Counter(x['uri'] for x in ns)
    print('nginx uri share: ' + ' '.join(f'{u}={c}({100 * c / max(len(ns), 1):.1f}%)' for u, c in sorted(uc.items())))
    nst = Counter(x['status'] for x in ns)
    att = sum(x['attempts'] for x in ns)
    print(f'nginx: entry={len(ns)} entry_rps={len(ns) / span:.1f} upstream_attempts={att}'
          f' attempts/entry={att / max(len(ns), 1):.3f} status=' + ','.join(f'{s}:{c}' for s, c in sorted(nst.items())))
    ac = Counter(x['attempts'] for x in ns)
    print('nginx attempts per entry: ' + ' '.join(f'{k}:{c}' for k, c in sorted(ac.items())) +
          f"  entries with a non-address in $upstream_addr (no live upstreams): {sum(1 for x in ns if x['noaddr'])}")
    ok1 = [x for x in ns if x['attempts'] == 1 and x['status'] == '200']
    print(f'nginx single-attempt 200s: n={len(ok1)} upstream_connect_time p99_ms={1000 * pct([x["connect"] for x in ok1], 99):.1f}'
          f' upstream_header_time p50_ms={1000 * pct([x["header"] for x in ok1], 50):.1f} p99_ms={1000 * pct([x["header"] for x in ok1], 99):.1f}')
    conc = [R.ngx_conc(a + i / 10) for i in range(span * 10)]
    print(f'nginx in-flight (100 ms instants): {stats(conc)}')
    e0, e1 = R.est_before(a), R.est_before(b)
    same = sum(1 for x in R.ngx if e0[0] <= x['start'] < e1[0])
    print(f'downstream connections (bpf ESTABLISHED, t={e0[0]:.1f}..{e1[0]:.1f}): {e1[1] - e0[1]}'
          f' nginx_entry_same_interval={same} connections/nginx_entry={(e1[1] - e0[1]) / max(same, 1):.3f}')
    w = [r for r in R.kern if a <= r['t'] < b]
    if w:
        print(f'kernel recvq (accept queue): {stats([r["recvq_total"] for r in w])}')
        print(f'kernel inuse (accepted, not closed): {stats([r["inuse"] for r in w])}')
        print(f'kernel util_pct (inuse/limit): {stats([100 * r["inuse"] / r["limit"] for r in w if r["limit"]])}')
        print(f'kernel backlog (Send-Q): {stats([r["sendq_8081"] for r in w])}')
        k0, k1 = R.kern_before(a), R.kern_before(b)
        print(f'nstat TcpExtListenOverflows +{int(k1["listen_overflows"] - k0["listen_overflows"])}'
              f' TcpExtListenDrops +{int(k1["listen_drops"] - k0["listen_drops"])}')
    aw = [v for t, v in R.acc if a <= t < b]
    cl = [v for t, v, _ in R.close if a <= t < b]
    print(f'bpf accept wait (by accept time): n={len(aw)} p50_ms={pct(aw, 50):.2f} p99_ms={pct(aw, 99):.2f}'
          f' max_ms={max(aw, default=float("nan")):.2f}')
    print(f'bpf accept-to-close (by close time): n={len(cl)} p50_ms={pct(cl, 50):.1f} p99_ms={pct(cl, 99):.1f}'
          f' max_ms={max(cl, default=float("nan")):.1f}')
    m = [x for x in MATCHED if a <= x[0] < b]
    print(f'accept-to-close matched to nginx lines by time (by close time): matched={len(m)} of closes={len(cl)}')
    for u in sorted(set(x[2] for x in m)):
        v = [x[1] for x in m if x[2] == u]
        sc = Counter(x[3] for x in m if x[2] == u)
        print(f'  uri={u}: n={len(v)} accept_to_close p50_ms={pct(v, 50):.1f} p99_ms={pct(v, 99):.1f} max_ms={max(v):.1f}'
              ' nginx_status=' + ','.join(f'{k}:{c}' for k, c in sorted(sc.items())))


try:
    with open(f'{d}/error.log') as f:
        el = f.read().splitlines()
    print()
    print('## nginx error.log lines in this scenario (second resolution, see slice.py)')
    for pat in ('upstream timed out', 'no live upstreams', 'upstream server temporarily disabled', 'connect() failed'):
        print(f'{pat}: {sum(1 for l in el if pat in l)}')
except FileNotFoundError:
    pass


window(0, SW)
window(SW, DUR)
window(DUR - 9, DUR)
