#!/usr/bin/env python3
"""Acceptance checks for every scenario, plus a per-second table per scenario.

Usage: scripts/analyze.py results <scenario> ...
Writes results/<scenario>/summary.txt (per-second table) and prints the checks to stdout
(run-all.sh saves them as results/checks.txt).

Time axis: t = seconds since the k6 test start (T0_MS logged by k6/load.js).
Sources per scenario directory:
  k6.csv                 http_req_duration of the "load" scenario; start = sample time - duration
  access.log             nginx; start = $msec - $request_time, end = $msec
  downstream_access.log  downstream; start (handler got the request), processing time, $request_id
  kernel_100ms.csv       ss / nstat in the downstream netns every 100 ms
  bpf.log                A = accept (accept wait), C = close (accept-to-close, established-to-close)
  error.log, error-info.log, nginx-T.txt, k6-env.txt, k6-stdout.txt (T0_MS, ADMIN_BEFORE, ADMIN_AFTER)

Each check prints: name | prediction | observed | pass / fail / info. A scenario whose run
had a host pause (k6 schedule gap or sampler round over 50 ms) is marked RUN INVALID. Where a prediction has no
stated tolerance, the one used is written in the prediction column.
"""
import bisect
import csv
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict

import expect as X

LOG = re.compile(r'^(\S+) (\S+) (\d+) (\S+) (\S+) "([^"]*)" "([^"]*)" "([^"]*)" "([^"]*)" "([^"]*)" (\S+)$')
ADDR = re.compile(r'^[0-9.]+:\d+$')


def pct(xs, p):
    if not xs:
        return float('nan')
    xs = sorted(xs)
    return xs[max(0, math.ceil(p / 100 * len(xs)) - 1)]


def med(xs):
    return statistics.median(xs) if xs else float('nan')


def num(x):
    try:
        return float(x)
    except ValueError:
        return None


def slope(pts):
    """least-squares slope of (t, y)"""
    if len(pts) < 2:
        return float('nan')
    mt = sum(t for t, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    den = sum((t - mt) ** 2 for t, _ in pts)
    return sum((t - mt) * (y - my) for t, y in pts) / den if den else float('nan')


class Run:
    def __init__(self, d):
        self.d = d
        self.meta = dict(l.strip().split('=', 1) for l in open(f'{d}/meta.env') if '=' in l)
        self.admin = {}
        for l in open(f'{d}/k6-stdout.txt'):
            p = l.strip().split(' ', 1)
            if len(p) == 2 and p[0] == 'T0_MS':
                self.t0_ms = int(p[1])
                self.t0 = self.t0_ms / 1000
            elif len(p) == 2 and p[0].startswith('ADMIN_'):
                self.admin[p[0]] = json.loads(p[1])
        t0 = self.t0
        self.drained = int(self.meta['T_DRAINED_MS']) / 1000 - t0
        self.k6env = dict(l.rstrip('\n').split('=', 1) for l in open(f'{d}/k6-env.txt') if '=' in l)

        self.k6 = []
        with open(f'{d}/k6.csv') as f:
            for r in csv.DictReader(f):
                if r['metric_name'] != 'http_req_duration' or r['scenario'] != 'load':
                    continue
                tg = dict(kv.split('=', 1) for kv in r['extra_tags'].split('&') if '=' in kv)
                dur = float(r['metric_value'])
                end = int(r['timestamp']) / 1000 - t0
                self.k6.append(dict(start=end - dur / 1000, dur=dur, status=r['status'], ep=tg.get('ep'),
                                    tenant=tg.get('tenant'), err=r['error_code']))

        self.ngx = []
        self.ngx_lines = []
        for line in open(f'{d}/access.log'):
            m = LOG.match(line.strip())
            if not m:
                continue
            msec, rt, st, uri, tn, ua, us, uct, uht, urt, rid = m.groups()
            # $msec, $request_time and T0 all have ms resolution: subtract in integer ms so that a
            # request starting exactly at the switch is not put just before it by float rounding
            end_ms = round(float(msec) * 1000) - self.t0_ms
            self.ngx.append(dict(start=(end_ms - round(float(rt) * 1000)) / 1000, end=end_ms / 1000, status=st, uri=uri, tenant=tn, ua=ua, us=us,
                                 sent=bool(ADDR.match(ua)), header=num(uht), rid=rid))
            self.ngx_lines.append(line.rstrip('\n'))
        self.by_rid = {x['rid']: x for x in self.ngx}
        self._ns = sorted(x['start'] for x in self.ngx)
        self._ne = sorted(x['end'] for x in self.ngx)

        self.ds = []
        for line in open(f'{d}/downstream_access.log'):
            p = line.split()
            if len(p) < 5:
                continue
            s = float(p[0]) - t0
            proc = float(p[1])
            self.ds.append(dict(start=s, proc=proc, end=s + proc / 1000, uri=p[2], rid=p[3], tenant=p[4]))
        self._ds = sorted(x['start'] for x in self.ds)
        self._de = sorted(x['end'] for x in self.ds)

        self.kern = []
        with open(f'{d}/kernel_100ms.csv') as f:
            for r in csv.DictReader(f):
                r = {k: float(v) for k, v in r.items()}
                r['t'] = r['ts_ms'] / 1000 - t0
                self.kern.append(r)

        self.acc, self.close, self.est = [], [], []
        for line in open(f'{d}/bpf.log'):
            p = line.split()
            if len(p) < 3:
                continue
            t = float(p[1]) - t0
            if p[0] == 'A':
                self.acc.append((t, int(p[2]) / 1000))
            elif p[0] == 'C' and len(p) > 4:
                a2c, e2c = int(p[2]) / 1000, int(p[4]) / 1000
                self.close.append((t, a2c, e2c - a2c))  # close time, accept-to-close ms, accept wait ms
            elif p[0] == 'E':
                self.est.append((t, int(p[2])))

        self.err = open(f'{d}/error.log').read().splitlines()
        try:
            self.err_info = open(f'{d}/error-info.log').read().splitlines()
        except FileNotFoundError:
            self.err_info = []

    def ngx_conc(self, x):
        return bisect.bisect_right(self._ns, x) - bisect.bisect_right(self._ne, x)

    def ds_conc(self, x):
        return bisect.bisect_right(self._ds, x) - bisect.bisect_right(self._de, x)

    def ds_conc_series(self, a, b, step=0.01):
        n = int(round((b - a) / step))
        return [self.ds_conc(a + i * step) for i in range(n)]

    def kw(self, a, b):
        return [r for r in self.kern if a <= r['t'] < b]

    def k6w(self, a, b):
        return [x for x in self.k6 if a <= x['start'] < b]


def ok(b):
    return 'pass' if b else 'fail'


def f1(x):
    return 'nan' if x is None or (isinstance(x, float) and math.isnan(x)) else f'{x:.1f}'


def norm_conf(path):
    out = []
    for l in open(path):
        l = l.strip()
        if not l or l.startswith('#') or l.startswith('nginx: '):
            continue
        out.append(l)
    return Counter(out)


def config_changes(R, base_conf):
    ch = []
    for k, v in X.COMMON_K6.items():
        if R.k6env.get(k, '') != v:
            ch.append(f'k6 {k}: {v} -> {R.k6env.get(k, "")}')
    for k in sorted(set(R.k6env) - set(X.COMMON_K6)):
        ch.append(f'k6 {k}: (unset) -> {R.k6env[k]}')
    before, after = R.admin.get('ADMIN_BEFORE', {}), R.admin.get('ADMIN_AFTER', {})
    for k, v in X.COMMON_ADMIN.items():
        if before.get(k) != v:
            ch.append(f'admin-before {k}: {v} -> {before.get(k)}')
        if after.get(k) != before.get(k):
            ch.append(f'admin {k}: {before.get(k)} -> {after.get(k)}')
    sc = norm_conf(f'{R.d}/nginx-T.txt')
    for l in sorted((base_conf - sc).elements()):
        ch.append(f'nginx - {l}')
    for l in sorted((sc - base_conf).elements()):
        ch.append(f'nginx + {l}')
    return ch


def msg_type(line):
    # "2026/10/06 09:22:47 [error] 79#79: *1320 <message>, client: ..." -> "[error] <message>"
    m = re.match(r'^\S+ \S+ (\[\w+\]) \d+#\d+: (?:\*\d+ )?(.*?)(?:, client: .*)?$', line)
    return re.sub(r'[0-9.]+', 'N', f'{m.group(1)} {m.group(2)}' if m else line)


def per_second(R, path):
    T = max(X.DUR, int(math.ceil(R.drained)))
    bk = defaultdict(list)
    for x in R.k6:
        bk[math.floor(x['start'])].append(x)
    bn = defaultdict(list)
    for x in R.ngx:
        bn[math.floor(x['start'])].append(x)
    bdc = defaultdict(list)
    for x in R.ds:
        bdc[math.floor(x['start'])].append(x['proc'])
    ba = defaultdict(list)
    for t, v in R.acc:
        ba[math.floor(t)].append(v)
    hdr = ['t', 'k6_n', 'k6_200', 'k6_429', 'k6_502', 'k6_504', 'k6_0', 'ngx_499', 'ngx_sent', 'ngx_infl',
           'ds_conc', 'recvq', 'inuse', 'acc_n', 'wait_p50', 'wait_max', 'proc_p50']
    with open(path, 'w') as f:
        f.write(f'# {R.d}\n')
        f.write('Bucketed by request start (k6, nginx), handler start (downstream proc_p50), accept time (bpf).\n')
        f.write('ngx_infl = nginx requests in flight at the start of the second; ds_conc = mean downstream\n')
        f.write('requests in service (downstream log, 10 ms instants); recvq / inuse = first 100 ms sample\n')
        f.write('in the second; wait = bpf accept wait (ms); proc = downstream processing time (ms).\n')
        f.write(' '.join(f'{h:>8}' for h in hdr) + '\n')
        for t in range(T):
            ks, ns = bk.get(t, []), bn.get(t, [])
            st = Counter(x['status'] for x in ks)
            kr = R.kw(t, t + 1)
            row = [t, len(ks), st.get('200', 0), st.get('429', 0), st.get('502', 0), st.get('504', 0), st.get('0', 0),
                   sum(1 for n in ns if n['status'] == '499'), sum(1 for n in ns if n['sent']), R.ngx_conc(t),
                   f1(statistics.mean(R.ds_conc_series(t, t + 1))),
                   int(kr[0]['recvq_total']) if kr else '', int(kr[0]['inuse']) if kr else '',
                   len(ba.get(t, [])), f1(pct(ba.get(t, []), 50)), f1(max(ba.get(t, []), default=float('nan'))),
                   f1(pct(bdc.get(t, []), 50))]
            f.write(' '.join(f'{str(v):>8}' for v in row) + '\n')


def dropped(R):
    try:
        m = json.load(open(f'{R.d}/k6-summary.json'))['metrics']
        return int(m.get('dropped_iterations', {}).get('count', 0))
    except (OSError, ValueError):
        return 'n/a'


def check_scenario(name, R, base_conf):
    E = X.S[name]
    rows = []

    def add(check, pred, obs, verdict):
        rows.append((check, pred, obs, verdict))

    sw, (ma, mb) = X.SWITCH, X.MEASURE
    pre_k6 = R.k6w(0, sw)
    pre_ngx = [x for x in R.ngx if 0 <= x['start'] < sw]
    meas_k6 = R.k6w(ma, mb)
    fixed = name == '11-b-fixed-key-limit'

    # 1. common starting point before the switch
    conc = R.ds_conc_series(0, sw)
    c_mean, c_max = statistics.mean(conc), max(conc)
    q_pre = [r['recvq_total'] for r in R.kw(0, sw)]
    proc_pre = [x['proc'] for x in R.ds if 0 <= x['start'] < sw]
    inuse_pre = [r['inuse'] for r in R.kw(0, sw)]
    k6st, ngst = Counter(x['status'] for x in pre_k6), Counter(x['status'] for x in pre_ngx)
    v = 'info' if fixed else None
    add('pre [0,8) downstream in service (downstream log, 10 ms instants)', '~10 (used: mean 9-11)',
        f'mean={c_mean:.2f} max={c_max} (kernel inuse mean={statistics.mean(inuse_pre):.2f})', v or ok(9 <= c_mean <= 11))
    aw = [w for t, w in R.acc if 0 <= t < sw]
    add('pre [0,8) accept wait (bpf, by accept time)', 'p99 <= 5 ms', f'p50={pct(aw, 50):.3f} ms p99={pct(aw, 99):.3f} ms max={max(aw):.3f} ms',
        v or ok(pct(aw, 99) <= 5))
    add('pre [0,8) accept queue (ss Recv-Q, 100 ms)', '(cross-check)', f'max={max(q_pre):.0f} samples={len(q_pre)}', 'info')
    add('pre [0,8) downstream processing time', '50 ms (used: median 45-55)',
        f'median={med(proc_pre):.2f} ms p99={pct(proc_pre, 99):.2f} ms n={len(proc_pre)}', v or ok(45 <= med(proc_pre) <= 55))
    add('pre [0,8) all 200', 'k6 and nginx: 200 only',
        f'k6 {dict(sorted(k6st.items()))} nginx {dict(sorted(ngst.items()))}',
        v or ok(set(k6st) == {'200'} and set(ngst) == {'200'}))

    # 2. fixed-key limit_req
    succ = 100 * sum(1 for x in meas_k6 if x['status'] == '200') / max(len(meas_k6), 1)
    stm = Counter(x['status'] for x in meas_k6)
    ok200 = [x['dur'] for x in meas_k6 if x['status'] == '200']
    p99 = pct(ok200, 99)
    if fixed:
        p429 = 100 * k6st.get('429', 0) / max(len(pre_k6), 1)
        q = [(r['t'], r['recvq_total']) for r in R.kw(sw + 1, X.DUR)]
        s = slope(q)
        lim = 0.2 * (200 - 20 / 0.12)
        add('fixed key: 429 share before the switch [0,8) (k6)', f'{E["pre_429"]}% +-5',
            f'{p429:.2f}% ({k6st.get("429", 0)}/{len(pre_k6)})', ok(abs(p429 - E['pre_429']) <= 5))
        add('fixed key: success after the switch [14,25) (k6)', f'{E["success"]}% +-5',
            f'{succ:.2f}% {dict(sorted(stm.items()))}', ok(abs(succ - E['success']) <= 5))
        add('fixed key: accept queue does not grow [9,25)', f'slope ~0 (used: |slope| <= 20% of the B slope = {lim:.1f}/s)',
            f'slope={s:.2f}/s max={max(y for _, y in q):.0f}', ok(abs(s) <= lim))
        add('p99 of 200 responses [14,25) (k6)', '(not predicted)', f'{p99:.1f} ms (n={len(ok200)})', 'info')

    # 3. configuration: only the listed item changed
    ch = sorted(config_changes(R, base_conf))
    exp = sorted(E['changes'])
    add('saved settings differ from the common point only by the listed item', '; '.join(exp), '; '.join(ch) or '(none)',
        ok(ch == exp))

    # 4. no 429 except the fixed-key scenario
    n429 = Counter(x['tenant'] for x in R.ngx if x['status'] == '429')
    if not fixed:
        add('429 over the whole run, all tenants (nginx)', '0', f'{sum(n429.values())} {dict(n429) if n429 else ""}'.strip(),
            ok(sum(n429.values()) == 0))

    # 5. ListenOverflows
    k0, k1 = R.kern[0], R.kern[-1]
    ov, dr = int(k1['listen_overflows'] - k0['listen_overflows']), int(k1['listen_drops'] - k0['listen_drops'])
    add('nstat TcpExtListenOverflows / ListenDrops over the run', '0 / 0', f'+{ov} / +{dr}', ok(ov == 0 and dr == 0))

    # 6. one upstream attempt per request
    multi = [x for x in R.ngx if ',' in x['ua'] or ' : ' in x['ua'] or ',' in x['us'] or ' : ' in x['us']]
    uas = Counter('<addr>' if x['sent'] else x['ua'] for x in R.ngx)
    add('$upstream_addr / $upstream_status with more than one entry', '0', f'{len(multi)} of {len(R.ngx)} ($upstream_addr: {dict(uas)})',
        ok(not multi))

    # 7. 1 request = 1 connection
    sent = sum(1 for x in R.ngx if x['sent'])
    add('bpf accepts vs nginx upstream sends (whole run incl. drain)', 'equal',
        f'accepts={len(R.acc)} sends={sent} downstream_log_lines={len(R.ds)}', ok(len(R.acc) == sent))

    # 8. onset, queue slope, success, p99
    if 'onset' in E:
        g = str(E['gave_up'])
        for uri, pt in E['onset'].items():
            xs = [x['start'] for x in R.ngx if x['status'] == g and (uri == 'all' or x['uri'] == uri)]
            first = min(xs) - sw if xs else float('nan')
            add(f'first {g} by request start, after the switch ({uri})', f'{pt:.1f} s +-1', f'{first:.2f} s',
                ok(abs(first - pt) <= 1))
        exp_s = E['arrivals'] - E['mu']
        q = [(r['t'], r['recvq_total']) for r in R.kw(sw + 1, X.DUR)]
        s = slope(q)
        add('accept queue slope [9,25) (ss Recv-Q)', f'{exp_s:.1f}/s +-20%', f'{s:.1f}/s (Recv-Q at 25 s: {q[-1][1]:.0f})',
            ok(abs(s - exp_s) <= 0.2 * exp_s))
        add('success rate [14,25) (k6)', f'{E["success"]}% (used: <= 5%)', f'{succ:.2f}% {dict(sorted(stm.items()))}', ok(succ <= 5))
        add('p99 of 200 responses [14,25) (k6)', '(not predicted)', f'{p99:.1f} ms (n={len(ok200)})', 'info')
    elif not fixed:
        add('success rate [14,25) (k6)', f'{E["success"]:.1f}% +-5', f'{succ:.2f}% {dict(sorted(stm.items()))}',
            ok(abs(succ - E['success']) <= 5))
        add('p99 of 200 responses [14,25) (k6)', f'~{E["p99"]} ms (used: +-20%)', f'{p99:.1f} ms (n={len(ok200)})',
            ok(abs(p99 - E['p99']) <= 0.2 * E['p99']))
        qm = [r['recvq_total'] for r in R.kw(sw, X.DUR)]
        n504 = sum(1 for x in R.ngx if x['status'] == '504')
        if 'queue_max' in E:
            add('accept queue after the switch [8,25)', f'<= {E["queue_max"]}', f'max={max(qm):.0f}', ok(max(qm) <= E['queue_max']))
            add('504 over the whole run (nginx)', '0', f'{n504}', ok(n504 == 0))
        else:
            add('accept queue after the switch [8,25)', '(not predicted)', f'max={max(qm):.0f}', 'info')
            add('504 over the whole run (nginx)', '(not predicted)', f'{n504}', 'info')
        n502 = sum(1 for x in R.ngx if x['status'] == '502' and not x['sent'])
        add('502 without an upstream attempt (max_conns) over the run', '(not predicted)', f'{n502}', 'info')

    # 9. work the downstream finished after nginx had already given up
    if E['kind'] == 'cause':
        g = str(E['gave_up'])
        firsts = [x['end'] for x in R.ngx if x['status'] == g]
        if firsts:
            f0 = min(firsts)
            after = [x for x in R.ds if x['end'] >= f0]
            orph = [x for x in after if x['rid'] in R.by_rid and R.by_rid[x['rid']]['status'] == g
                    and R.by_rid[x['rid']]['end'] <= x['end']]
            nomatch = sum(1 for x in after if x['rid'] not in R.by_rid)
            frac = 100 * len(orph) / max(len(after), 1)
            add(f'downstream finished after nginx had returned {g} ($request_id), from the first {g} (t={f0:.2f} s) to the end of the drain',
                '~100% (used: >= 95%)', f'{frac:.2f}% ({len(orph)}/{len(after)}, unmatched ids={nomatch})', ok(frac >= 95))
            b = defaultdict(lambda: [0, 0])
            for x in R.ds:
                n = R.by_rid.get(x['rid'])
                k = math.floor(x['end'])
                b[k][1] += 1
                if n and n['status'] == g and n['end'] <= x['end']:
                    b[k][0] += 1
            ser = ' '.join(f'{k}:{100 * v[0] / v[1]:.0f}%' for k, v in sorted(b.items()) if k >= sw)
            add('  same, per second of downstream finish (t:share)', '', ser, 'info')
        else:
            add(f'downstream finished after nginx had returned {g}', '~100%', f'no {g} in the run', 'fail')

    # 10. telling the causes apart (cause scenarios A-D)
    if E['kind'] == 'cause' and name != '05-b-front-cuts':
        post = [x for x in R.ds if ma <= x['start'] < mb]
        pre_l = [x['proc'] for x in R.ds if 0 <= x['start'] < sw and x['uri'] == '/api/light']
        conc_m = R.ds_conc_series(ma, mb)
        cm, cx = statistics.mean(conc_m), max(conc_m)
        nm = [x for x in R.ngx if ma <= x['start'] < mb]
        if E['cause'] == 'A':
            post_l = [x['proc'] for x in post if x['uri'] == '/api/light']
            post_h = [x['proc'] for x in post if x['uri'] == '/api/heavy']
            add('A: /api/light processing time unchanged [14,25) (downstream log)', 'post median within +-10% of pre',
                f'pre={med(pre_l):.2f} ms post={med(post_l):.2f} ms', ok(abs(med(post_l) - med(pre_l)) <= 0.1 * med(pre_l)))
            add('A: /api/heavy processing time = its setting [14,25) (downstream log)', '300 ms (used: +-10%)',
                f'post median={med(post_h):.2f} ms (no /api/heavy before the switch)', ok(abs(med(post_h) - 300) <= 30))
            hp = 100 * sum(1 for x in pre_ngx if x['uri'] == '/api/heavy') / max(len(pre_ngx), 1)
            hm = 100 * sum(1 for x in nm if x['uri'] == '/api/heavy') / max(len(nm), 1)
            add('A: /api/heavy share of nginx entries', '0% -> 40% (used: +-5)', f'pre={hp:.2f}% post={hm:.2f}%',
                ok(hp == 0 and abs(hm - 40) <= 5))
        if E['cause'] == 'B':
            pp = [x['proc'] for x in post]
            add('B: processing time rises [14,25) (downstream log)', '50 -> 120 ms (used: +-10%)',
                f'pre={med(proc_pre):.2f} ms post={med(pp):.2f} ms', ok(abs(med(pp) - 120) <= 12))
            add('B: downstream in service pinned at 20 [14,25) (downstream log)', '20 (used: mean >= 19, max 20)',
                f'mean={cm:.2f} max={cx}', ok(cm >= 19 and cx == 20))
        if E['cause'] == 'C':
            pp = [x['proc'] for x in post]
            add('C: processing time unchanged [14,25) (downstream log)', 'post median within +-10% of pre',
                f'pre={med(proc_pre):.2f} ms post={med(pp):.2f} ms', ok(abs(med(pp) - med(proc_pre)) <= 0.1 * med(proc_pre)))
            add('C: downstream in service pinned at 8 [14,25) (downstream log)', '8 (used: mean >= 7.6, max 8)',
                f'mean={cm:.2f} max={cx}', ok(cm >= 7.6 and cx == 8))
        if E['cause'] == 'D':
            tp = Counter(x['tenant'] for x in pre_ngx)
            tm = Counter(x['tenant'] for x in nm)
            span = mb - ma
            rates = [c / span for c in tm.values()]
            add('D: tenant kinds (nginx $http_x_tenant)', '10 -> 30', f'pre={len(tp)} post={len(tm)}',
                ok(len(tp) == 10 and len(tm) == 30))
            add('D: per-tenant rate unchanged [14,25) (nginx entries)', '20 r/s each (used: every tenant 16-24)',
                f'min={min(rates):.1f} max={max(rates):.1f} mean={statistics.mean(rates):.1f} r/s'
                f' (pre mean={statistics.mean(c / sw for c in tp.values()):.1f})', ok(all(16 <= r <= 24 for r in rates)))
            add('D: total rate [14,25) (nginx entries)', '200 -> 600 r/s (used: +-5%)',
                f'pre={len(pre_ngx) / sw:.1f} post={len(nm) / span:.1f}', ok(abs(len(nm) / span - 600) <= 30))

    # 11. reader-side wait vs accept wait, per 1 s interval and per endpoint
    #   reader: median $upstream_header_time of nginx lines logged in the second ($msec) minus the
    #           median processing time of downstream lines finishing in the second (start + processing)
    #   bpf:    median accept wait (established-to-close minus accept-to-close) of connections closed
    #           in the second. The endpoint of a bpf close comes from the downstream log lines whose
    #           start and end lie within 0.5 ms of the connection's accept and close; it is used when all
    #           such lines have the same endpoint (lines that share start and end share the processing
    #           time, so they can only be ambiguous between connections, not between endpoints).
    #   Compared: intervals with a bpf wait >= 10 ms that end before the first 504 / 499 ($msec),
    #   with >= 5 samples on each side.
    by_end = sorted((x['end'], i) for i, x in enumerate(R.ds))
    ends = [e for e, _ in by_end]
    bb = defaultdict(list)
    matched = 0
    for t, a2c, w in R.close:
        lo, hi = bisect.bisect_left(ends, t - 0.0005), bisect.bisect_right(ends, t + 0.0005)
        uris = {R.ds[i]['uri'] for _, i in by_end[lo:hi] if abs((t - a2c / 1000) - R.ds[i]['start']) <= 0.0005}
        if len(uris) == 1:
            bb[(math.floor(t), uris.pop())].append(w)
            matched += 1
    bh, bp = defaultdict(list), defaultdict(list)
    for x in R.ngx:
        if x['header'] is not None:
            bh[(math.floor(x['end']), x['uri'])].append(x['header'] * 1000)
    for x in R.ds:
        bp[(math.floor(x['end']), x['uri'])].append(x['proc'])
    gave = [x['end'] for x in R.ngx if x['status'] in ('504', '499')]
    limit_t = min(gave) if gave else float('inf')
    res = []
    for key in sorted(set(bh) & set(bp) & set(bb)):
        s_, uri = key
        if s_ < 0 or s_ + 1 > limit_t or min(len(bh[key]), len(bp[key]), len(bb[key])) < 5:
            continue
        r, b = med(bh[key]) - med(bp[key]), med(bb[key])
        if b < 10:
            continue
        res.append((s_, uri, r, b, len(bh[key]), len(bb[key]), abs(r - b) <= 0.1 * b))
    fails = [x for x in res if not x[6]]
    add('reader-side wait vs accept wait, per 1 s and per endpoint (wait >= 10 ms, before the first 504/499)',
        'within 10% in every compared interval',
        f'{len(res) - len(fails)}/{len(res)} within 10% (bpf closes matched to downstream lines: {matched}/{len(R.close)};'
        f' first 504/499 at t={limit_t:.2f}){"" if res else "; no interval qualifies"}', ok(not fails) if res else 'info')
    if res:
        add('  t endpoint: reader/bpf ms (n_nginx_with_header,n_bpf), * = outside 10%', '',
            ' '.join(f'{s_}{u.replace("/api/", " ")}:{r:.1f}/{b:.1f}({nh},{nb}){"" if p else "*"}'
                     for s_, u, r, b, nh, nb, p in res), 'info')

    add('k6 dropped iterations / requests sent', '', f'{dropped(R)} / {len(R.k6)}', 'info')
    # pauses of the whole host show up as gaps in the evenly spaced k6 schedule and as long
    # sampler cycles (one ss / nstat / GET /admin round normally takes a few ms)
    st = sorted(x['start'] for x in R.k6 if 0 <= x['start'] < X.DUR)
    gaps = sorted(((b - a) * 1000, a) for a, b in zip(st, st[1:]))[-3:]
    sm = sorted((r['sample_ms'], r['t']) for r in R.kern if 0 <= r['t'] < X.DUR)[-3:]
    paused = gaps[-1][0] > 50 or sm[-1][0] > 50
    add('host pause: largest gaps between k6 request starts [0,25) (ms @ t)', '<= 50 ms',
        ', '.join(f'{g:.0f}@{t:.2f}' for g, t in reversed(gaps)), ok(gaps[-1][0] <= 50))
    add('host pause: longest sampler rounds [0,25) (ms @ t)', '<= 50 ms',
        ', '.join(f'{g:.0f}@{t:.2f}' for g, t in reversed(sm)), ok(sm[-1][0] <= 50))
    return rows, paused


def strings(name, R):
    lines = [f'### {name}']
    for st in ('504', '502', '499'):
        ex = [l for l, x in zip(R.ngx_lines, R.ngx) if x['status'] == st]
        if ex:
            lines.append(f'access.log {st} (first of {len(ex)}): {ex[0]}')
    for label, src in (('error.log', R.err), ('error-info.log', R.err_info)):
        c = Counter(msg_type(l) for l in src)
        for m, n in c.most_common():
            first = next(l for l in src if msg_type(l) == m)
            lines.append(f'{label} x{n}: {first}')
    ke = Counter((x['status'], x['err']) for x in R.k6 if x['status'] != '200')
    if ke:
        lines.append('k6 non-200 (status/error_code:count): ' + ' '.join(f'{s}/{e or "-"}:{n}' for (s, e), n in sorted(ke.items())))
    return lines


def main():
    res_dir, names = sys.argv[1], sys.argv[2:]
    base_conf = norm_conf(f'{res_dir}/00-base-nginx-T.txt')
    tot, strs, invalid = Counter(), [], []
    for n in names:
        R = Run(f'{res_dir}/{n}')
        per_second(R, f'{res_dir}/{n}/summary.txt')
        rows, paused = check_scenario(n, R, base_conf)
        if paused:
            invalid.append(n)
        print(f'## {n}' + ('   RUN INVALID: host pause (rerun this scenario)' if paused else ''))
        print('check | prediction | observed | result')
        for r in rows:
            print(' | '.join(r))
            tot[r[3]] += 1
        print()
        strs += strings(n, R)
    print('## strings (first line of each kind, per scenario)')
    print('\n'.join(strs))
    print()
    print('## totals: ' + ' '.join(f'{k}={v}' for k, v in sorted(tot.items())))
    print('## invalid runs (host pause): ' + (' '.join(invalid) if invalid else 'none'))


main()
