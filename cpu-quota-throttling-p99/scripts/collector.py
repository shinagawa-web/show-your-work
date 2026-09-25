import sys, time

cg, interval, duration = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])


def stat():
    return dict(l.split() for l in open(cg + "/cpu.stat"))


def pressure(path):
    out = {}
    for l in open(path):
        kind, *kv = l.split()
        for x in kv:
            k, v = x.split("=")
            out[kind + "_" + k] = v
    return out


keys = ["usage_usec", "nr_periods", "nr_throttled", "throttled_usec"]
print("t_unix_ns,t_mono_ns," + ",".join(keys) + ",cg_some_avg10,cg_some_total,cg_full_avg10,cg_full_total,sys_some_avg10,sys_some_total", flush=True)
end = time.monotonic() + duration
nxt = time.monotonic()
while True:
    s, p, q = stat(), pressure(cg + "/cpu.pressure"), pressure("/proc/pressure/cpu")
    print(",".join([str(time.time_ns()), str(time.monotonic_ns())] + [s[k] for k in keys] + [p["some_avg10"], p["some_total"], p["full_avg10"], p["full_total"], q["some_avg10"], q["some_total"]]), flush=True)
    if time.monotonic() >= end:
        break
    nxt += interval
    time.sleep(max(0, nxt - time.monotonic()))
