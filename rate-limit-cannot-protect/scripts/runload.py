"""Loaders shared by analyze.py and onset.py. Time axis: t = seconds since the first k6
request started (k6 sample time minus http_req_duration)."""
import bisect
import csv
import math
import re


def pct(xs, p):
    if not xs:
        return float('nan')
    xs = sorted(xs)
    return xs[max(0, math.ceil(p / 100 * len(xs)) - 1)]


def _tags(extra):
    return dict(kv.split('=', 1) for kv in extra.split('&') if '=' in kv) if extra else {}


LOG = re.compile(r'^(\S+) (\S+) (\d+) (\S+) (\S+) "([^"]*)" "([^"]*)" "([^"]*)" "([^"]*)" "([^"]*)"$')


ADDR = re.compile(r'^[0-9.]+:\d+$')


def _f(x):
    try:
        return float(x)
    except ValueError:
        return None


class Run:
    def __init__(self, d):
        # k6: (start, dur_ms, status, ep, tenant, end)
        raw = []
        with open(f'{d}/k6.csv') as f:
            for r in csv.DictReader(f):
                if r['metric_name'] != 'http_req_duration' or r['scenario'] == 'switch':
                    continue
                tg = _tags(r.get('extra_tags', ''))
                dur = float(r['metric_value'])
                end = int(r['timestamp']) / 1000
                raw.append((end - dur / 1000, dur, r['status'], tg.get('ep', '?'), tg.get('tenant', '?'), end))
        self.t0 = t0 = min(x[0] for x in raw)
        self.k6 = [(s - t0, dur, st, ep, tn, e - t0) for s, dur, st, ep, tn, e in raw]

        # nginx: dict per request; start = $msec - $request_time
        self.ngx = []
        with open(f'{d}/access.log') as f:
            for line in f:
                m = LOG.match(line.strip())
                if not m:
                    continue
                msec, rt, st, uri, tn, ua, us, uct, uht, urt = m.groups()
                end = float(msec) - t0
                # $upstream_addr lists every server tried, separated by ', ' (' : ' across internal redirects).
                # When no server could be chosen ("no live upstreams") it holds the upstream group name
                # instead of an address; that entry is not an attempt.
                parts = [] if ua == '-' else re.split(r', | : ', ua)
                att = sum(1 for x in parts if ADDR.match(x))
                self.ngx.append(dict(start=end - float(rt), end=end, status=st, uri=uri, tenant=tn,
                                     attempts=att, noaddr=len(parts) - att,
                                     connect=_f(uct), header=_f(uht), response=_f(urt)))
        self._starts = sorted(x['start'] for x in self.ngx)
        self._ends = sorted(x['end'] for x in self.ngx)

        # kernel samples (100 ms)
        self.kern = []
        with open(f'{d}/kernel_100ms.csv') as f:
            for r in csv.DictReader(f):
                r = {k: float(v) for k, v in r.items()}
                r['t'] = r['ts_ms'] / 1000 - t0
                self.kern.append(r)

        # bpftrace
        self.acc, self.close, self.est = [], [], []
        with open(f'{d}/bpf.log') as f:
            for line in f:
                p = line.split()
                if len(p) < 3:
                    continue
                t = float(p[1]) - t0
                if p[0] == 'A':
                    self.acc.append((t, int(p[2]) / 1000))  # accept wait, ms
                elif p[0] == 'C':
                    # accept -> close, ms; established -> close, ms (None in older logs)
                    self.close.append((t, int(p[2]) / 1000, int(p[4]) / 1000 if len(p) > 4 else None))
                elif p[0] == 'E':
                    self.est.append((t, int(p[2])))  # cumulative passive ESTABLISHED count

    def ngx_conc(self, x):
        """nginx requests in flight at instant x (start <= x < end)"""
        return bisect.bisect_right(self._starts, x) - bisect.bisect_right(self._ends, x)

    def kern_at(self, x):
        """first kernel sample at or after x (None if none within 1 s)"""
        i = bisect.bisect_left([r['t'] for r in self.kern], x)
        if i < len(self.kern) and self.kern[i]['t'] - x < 1:
            return self.kern[i]
        return None

    def kern_before(self, x):
        c = [r for r in self.kern if r['t'] <= x]
        return c[-1] if c else self.kern[0]

    def est_before(self, x):
        c = [e for e in self.est if e[0] <= x]
        return c[-1] if c else self.est[0]
