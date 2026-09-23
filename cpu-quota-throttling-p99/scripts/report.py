import json, os, sys


def load(d):
    return json.load(open(os.path.join(d, "summary.json")))


def get(o, path):
    for k in path.split("."):
        o = o.get(k) if isinstance(o, dict) else None
    return o


def show(d, keys):
    o = load(d)
    print(json.dumps({k: get(o, k) for k in keys}, ensure_ascii=False, indent=1))


COLS = [
    ("host", "host"), ("cpu_model", "cpu_model"),
    ("cpus", "cond.cpus"), ("W", "cond.W"), ("RPS", "cond.rps"), ("mode", "cond.mode"), ("dur_s", "cond.dur"),
    ("util_60s_%", "util_window_pct"), ("util_1s_max_%", "util_1s_pct.max"),
    ("throttle_rate", "throttle_rate"), ("p50_ms", "latency_ms.p50"), ("p99_ms", "latency_ms.p99"), ("max_ms", "latency_ms.max"),
    ("cpus_in_use_mean", "cpus_in_use_10ms.mean"), ("cpus_in_use_max", "cpus_in_use_10ms.max"),
    ("recvq_max", "recvq.max"), ("recvq_mean_first10s", "recvq.mean_first10s"), ("recvq_mean_last10s", "recvq.mean_last10s"),
    ("req_crossing_throttle", "requests_crossing_throttle.n_ge1"), ("n", "requests_crossing_throttle.n"), ("errors", "n_errors"),
]


def table(dirs):
    print("| run | " + " | ".join(c for c, _ in COLS) + " |")
    print("|" + "---|" * (len(COLS) + 1))
    for d in dirs:
        o = load(d)
        print(f"| {os.path.basename(d.rstrip('/'))} | " + " | ".join(str(get(o, p)) for _, p in COLS) + " |")


def md_rows(title, rows, head=("metric", "value")):
    out = [f"### {title}", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def pct(a, b):
    return round(a / b * 100, 2) if b else None


def md_s3(d):
    o = load(d)
    lat = o["latency_ms"]
    return md_rows(f"Section 3 (repro): {os.path.basename(d.rstrip('/'))}", [
        ("condition", f"--cpus {o['cond']['cpus']}, W={o['cond']['W']}, RPS {o['cond']['rps']} {o['cond']['mode']}, {o['cond']['dur']}s"),
        ("host / CPU", f"{o.get('host')} / {o.get('cpu_model')}"),
        ("60s-average utilization (% of limit)", o["util_window_pct"]),
        ("latency p50 / p99 / max (ms)", f"{lat['p50']} / {lat['p99']} / {lat['max']}"),
        ("throttle rate (nr_throttled / nr_periods)", f"{o['throttle_rate']} ({o['delta_window']['nr_throttled']} / {o['delta_window']['nr_periods']})"),
        ("requests / errors", f"{o['n_requests']} / {o['n_errors']}"),
    ])


def md_s4(d):
    o = load(d)
    p, rq = o["pressure"], o["recvq"]
    t, n = p["10ms_overlapping_kprobe_throttle"], p["10ms_not_overlapping"]
    return md_rows("Section 4 (investigation)", [
        ("1s-average utilization max (% of limit)", f"{o['util_1s_pct']['max']} ({o['util_1s_pct']['n_ge90']} of {o['util_1s_pct']['n']} seconds at or above 90%)"),
        ("nr_throttled increase", o["delta_window"]["nr_throttled"]),
        ("cpu.pressure some avg10 max, container / system (%)", f"{p['cg_some_avg10_max']} / {p['sys_some_avg10_max']}"),
        ("container some share of 10ms intervals overlapping a throttle (%)", f"{pct(t['cg_some_us'], t['elapsed_us'])} ({t['cg_some_us']} / {t['elapsed_us']} us)"),
        ("container some share of other 10ms intervals (%)", f"{pct(n['cg_some_us'], n['elapsed_us'])} ({n['cg_some_us']} / {n['elapsed_us']} us)"),
        ("Recv-Q max / mean", f"{rq['max']} / {rq['mean']}"),
    ])


def md_s6(d):
    o = load(d)
    k, c = o["kprobe"], o["requests_crossing_throttle"]
    u, pc = k["union_len_ms"] or {}, k["per_cpu_len_ms"] or {}
    s = md_rows("Section 6 (mechanism)", [
        ("throttle length, union of CPUs, median / max (ms)", f"{u.get('p50')} / {u.get('max')} ({k['throttle_windows_in_window_union']} throttles)"),
        ("throttle length, per CPU, median / max (ms)", f"{pc.get('p50')} / {pc.get('max')} ({k['throttle_events_in_window_per_cpu']} events)"),
        ("throttled_usec / nr_throttled (us)", o["throttled_usec_per_nr_throttled"]),
        ("requests crossing at least one throttle", f"{c['n_ge1']} of {c['n']}"),
    ])
    s += "\n" + md_rows("Requests at or above p99", [(r["lat_ms"], r["kprobe_crossings"], r["nr_throttled_delta_10ms"], r["recvq_at_send"]) for r in o["slow_ge_p99"]],
                         head=("latency (ms)", "throttles crossed (kprobe)", "nr_throttled increase (10ms series)", "Recv-Q at send"))
    s += "\n" + md_rows("Latency histogram (10ms bins)", [(f"{b}-{int(b) + 10}", v) for b, v in o["hist_latency_10ms"].items()], head=("latency (ms)", "requests"))
    return s


def md_s10(path):
    rows = [json.loads(l) for l in open(path) if l.strip()]
    cols = ["env", "limit_setting", "host_cgroup", "host_cpu.max", "host_cpu.stat", "host_cpu.stat_after_exec", "pod_cgroup", "pod_cpu.max", "inside_cpu.max", "inside_cpu.stat", "cpu.max_same", "cpu.stat_same"]
    out = ["### Section 10 (environments)", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r.get(c, "-")).strip() for c in cols) + " |" for r in rows if "env" in r]
    checks = [r for r in rows if "check" in r]
    if checks:
        keys = [k for k in checks[0] if k != "check"]
        out += ["", "### Host state before and after k3s", "", "| item | " + " | ".join(r["check"] for r in checks) + " |", "|" + "---|" * (len(checks) + 1)]
        out += ["| " + k + " | " + " | ".join(str(r.get(k, "")).strip() or "(none)" for r in checks) + " |" for k in keys]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    if sys.argv[1] == "md":
        print({"s3": md_s3, "s4": md_s4, "s6": md_s6, "s10": md_s10}[sys.argv[2]](sys.argv[3]), end="")
        sys.exit(0)
    if sys.argv[1] == "show":
        show(sys.argv[2], sys.argv[3:])
    elif sys.argv[1] == "table":
        table(sys.argv[2:])
