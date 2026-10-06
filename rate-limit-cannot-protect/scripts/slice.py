#!/usr/bin/env python3
"""Copy the part of the shared logs that belongs to one scenario into results/<scenario>/.

Window: from just before k6 started (T_START_MS) to when the downstream had worked off its
accept queue after k6 exited (T_DRAINED_MS), plus 300 ms. Scenarios run one after another,
so the windows do not overlap.
nginx error logs have second resolution (UTC in the container): a line stamped second s is
kept when [s, s + 1 s) overlaps the window.
"""
import calendar
import sys
import time

raw, out = sys.argv[1], sys.argv[2]
meta = dict(l.strip().split('=', 1) for l in open(f'{out}/meta.env') if '=' in l)
lo, hi = int(meta['T_START_MS']), int(meta['T_DRAINED_MS']) + 300


def keep(src, dst, ts_ms, header=False):
    try:
        f = open(f'{raw}/{src}')
    except FileNotFoundError:
        return
    with f, open(f'{out}/{dst}', 'w') as g:
        for i, line in enumerate(f):
            if header and i == 0:
                g.write(line)
                continue
            try:
                t = ts_ms(line)
            except (ValueError, IndexError):
                continue
            if lo <= t <= hi:
                g.write(line)


def err_ms(line):
    t = calendar.timegm(time.strptime(line[:19], '%Y/%m/%d %H:%M:%S')) * 1000
    return lo if t + 999 >= lo and t <= hi else t


keep('access.log', 'access.log', lambda l: float(l.split(' ', 1)[0]) * 1000)
keep('downstream_access.log', 'downstream_access.log', lambda l: float(l.split(' ', 1)[0]) * 1000)
keep('kernel_100ms.csv', 'kernel_100ms.csv', lambda l: float(l.split(',', 1)[0]), header=True)
keep('bpf.log', 'bpf.log', lambda l: float(l.split(' ')[1]) * 1000)
keep('error.log', 'error.log', err_ms)
keep('error-info.log', 'error-info.log', err_ms)
