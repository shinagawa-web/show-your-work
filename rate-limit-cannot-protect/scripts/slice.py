#!/usr/bin/env python3
"""Copy the part of the shared nginx logs, kernel samples and bpftrace log that belongs to one scenario.
nginx error.log has second resolution (UTC in the container), so its slice is widened to whole seconds."""
import calendar
import sys
import time

raw, out, start, end = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
lo, hi = start - 1000, end + 1500


def keep(src, dst, ts_ms, header):
    with open(f'{raw}/{src}') as f, open(f'{out}/{dst}', 'w') as g:
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


keep('access.log', 'access.log', lambda l: float(l.split(' ', 1)[0]) * 1000, False)
keep('kernel_100ms.csv', 'kernel_100ms.csv', lambda l: float(l.split(',', 1)[0]), True)
keep('bpf.log', 'bpf.log', lambda l: float(l.split(' ')[1]) * 1000, False)



def _err_ms(l):
    # a line stamped second t covers [t, t + 1000) ms; keep it if that overlaps [lo, hi]
    t = calendar.timegm(time.strptime(l[:19], '%Y/%m/%d %H:%M:%S')) * 1000
    return lo if t + 999 >= lo and t <= hi else t


keep('error.log', 'error.log', _err_ms, False)
