import csv, glob, json, os, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(d):
    return json.load(open(os.path.join(d, "summary.json")))


def get(o, path):
    for k in path.split("."):
        o = o.get(k) if isinstance(o, dict) else None
    return o


def conditions(section):
    return [r["name"] for r in csv.DictReader(open(os.path.join(HERE, "conditions.tsv")), delimiter="\t") if r["section"] == section]


COLS = [
    ("host", "host"), ("cpu_model", "cpu_model"),
    ("cpus", "cond.cpus"), ("W", "cond.W"), ("RPS", "cond.rps"), ("mode", "cond.mode"), ("dur_s", "cond.dur"),
    ("util_run_avg_%", "util_window_pct"), ("util_1s_max_%", "util_1s_pct.max"),
    ("throttle_rate", "throttle_rate"), ("p50_ms", "latency_ms.p50"), ("p99_ms", "latency_ms.p99"), ("max_ms", "latency_ms.max"),
    ("cpus_in_use_mean", "cpus_in_use_10ms.mean"), ("cpus_in_use_max", "cpus_in_use_10ms.max"),
    ("recvq_max", "recvq.max"), ("recvq_mean_first10s", "recvq.mean_first10s"), ("recvq_mean_last10s", "recvq.mean_last10s"),
    ("req_crossing_throttle_kprobe", "requests_crossing_throttle.n_ge1"),
    ("req_crossing_throttle_nr_throttled", "requests_crossing_throttle.n_ge1_nr_throttled_10ms"),
    ("n", "requests_crossing_throttle.n"), ("errors", "n_errors"),
]


def table(dirs):
    out = ["| run | " + " | ".join(c for c, _ in COLS) + " |", "|" + "---|" * (len(COLS) + 1)]
    for d in dirs:
        o = load(d)
        out.append(f"| {os.path.basename(d.rstrip('/'))} | " + " | ".join(str(get(o, p)) for _, p in COLS) + " |")
    return "\n".join(out) + "\n"


def md_rows(title, rows, head=("metric", "value")):
    out = [f"### {title}", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def pct(a, b):
    return round(a / b * 100, 2) if b else None


def md_s3(d):
    o = load(d)
    lat = o["latency_ms"]
    return md_rows("Section 3 (repro)", [
        ("condition", f"--cpus {o['cond']['cpus']}, W={o['cond']['W']}, RPS {o['cond']['rps']} {o['cond']['mode']}, {o['cond']['dur']}s"),
        ("host / CPU", f"{o.get('host')} / {o.get('cpu_model')}"),
        ("utilization averaged over the run (% of limit)", o["util_window_pct"]),
        ("latency p50 / p99 / max (ms)", f"{lat['p50']} / {lat['p99']} / {lat['max']}"),
        ("throttle rate (nr_throttled / nr_periods)", f"{o['throttle_rate']} ({o['delta_window']['nr_throttled']} / {o['delta_window']['nr_periods']})"),
        ("requests / errors", f"{o['n_requests']} / {o['n_errors']}"),
    ])


def md_s4(d):
    o = load(d)
    p, rq = o["pressure"], o["recvq"]
    t, n = p["10ms_overlapping_throttle"], p["10ms_not_overlapping"]
    return md_rows("Section 4 (investigation)", [
        ("1s-average utilization max (% of limit)", f"{o['util_1s_pct']['max']} ({o['util_1s_pct']['n_ge90']} of {o['util_1s_pct']['n']} seconds at or above 90%)"),
        ("nr_throttled increase", o["delta_window"]["nr_throttled"]),
        ("cpu.pressure some avg10 max, container / system (%)", f"{p['cg_some_avg10_max']} / {p['sys_some_avg10_max']}"),
        ("throttle intervals taken from", p["throttle_interval_source"]),
        ("container some share of 10ms intervals overlapping a throttle (%)", f"{pct(t['cg_some_us'], t['elapsed_us'])} ({t['cg_some_us']} / {t['elapsed_us']} us)"),
        ("container some share of other 10ms intervals (%)", f"{pct(n['cg_some_us'], n['elapsed_us'])} ({n['cg_some_us']} / {n['elapsed_us']} us)"),
        ("Recv-Q max / mean", f"{rq['max']} / {rq['mean']}"),
    ])


def md_s6(d):
    o = load(d)
    k, c = o["kprobe"], o["requests_crossing_throttle"]
    if isinstance(k, dict):
        u, pc = k["union_len_ms"] or {}, k["per_cpu_len_ms"] or {}
        union = f"{u.get('p50')} / {u.get('max')} ({k['throttle_windows_in_window_union']} throttles)"
        per_cpu = f"{pc.get('p50')} / {pc.get('max')} ({k['throttle_events_in_window_per_cpu']} events)"
    else:
        union = per_cpu = k
    kc = c["n_ge1"]
    s = md_rows("Section 6 (mechanism)", [
        ("throttle length (kprobe), union of CPUs, median / max (ms)", union),
        ("throttle length (kprobe), per CPU, median / max (ms)", per_cpu),
        ("throttled_usec / nr_throttled (us)", o["throttled_usec_per_nr_throttled"]),
        ("requests crossing at least one throttle (kprobe)", f"{kc} of {c['n']}" if isinstance(kc, int) else kc),
        ("requests with nr_throttled increasing while in flight (10ms cpu.stat)", f"{c['n_ge1_nr_throttled_10ms']} of {c['n']}"),
    ])
    s += "\n" + md_rows("Requests at or above p99", [(r["lat_ms"], r["kprobe_crossings"], r["nr_throttled_delta_10ms"], r["recvq_at_send"]) for r in o["slow_ge_p99"]],
                        head=("latency (ms)", "throttles crossed (kprobe)", "nr_throttled increase (10ms cpu.stat)", "Recv-Q at send"))
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
        out += ["", "### Host state for section 10", "", "| item | " + " | ".join(r["check"] for r in checks) + " |", "|" + "---|" * (len(checks) + 1)]
        out += ["| " + k + " | " + " | ".join(str(r.get(k, "")).strip() or "(none)" for r in checks) + " |" for k in keys]
    return "\n".join(out) + "\n"


def md_table(results, section, title):
    runs = [os.path.join(results, section, n) for n in conditions(section)]
    runs = [d for d in runs if os.path.exists(os.path.join(d, "summary.json"))]
    if not runs:
        return None
    s3 = os.path.join(results, "s3")
    if os.path.exists(os.path.join(s3, "summary.json")):
        runs.insert(0, s3)
    return f"### {title}\n\n" + table(runs)


def summary(results):
    parts = ["# cpu-quota-throttling-p99 summary\n"]
    envs = sorted(glob.glob(os.path.join(results, "environment", "*.txt")))
    if envs:
        keys = ["host", "cpu_model", "nproc", "kernel", "cgroup_fs", "docker_cgroup_driver", "bpftrace_usable", "bpftrace_note"]
        rows = []
        for f in envs:
            kv = dict(l.rstrip("\n").split("=", 1) for l in open(f) if "=" in l)
            rows.append([os.path.basename(f)[:-4]] + [kv.get(k, "") for k in keys])
        parts.append("### Environment\n\n| run | " + " | ".join(keys) + " |\n|" + "---|" * (len(keys) + 1) + "\n" +
                     "\n".join("| " + " | ".join(r) + " |" for r in rows) + "\n")
    s3 = os.path.join(results, "s3")
    if os.path.exists(os.path.join(s3, "summary.json")):
        parts += [md_s3(s3), md_s4(s3), md_s6(s3)]
    parts.append(md_table(results, "s5", "Section 5 (cause)" + (", s3 as the first row" if os.path.exists(os.path.join(s3, "summary.json")) else "")))
    parts.append(md_table(results, "s9", "Section 9 (verification)" + (", s3 as the first row" if os.path.exists(os.path.join(s3, "summary.json")) else "")))
    env = os.path.join(results, "s10", "env.jsonl")
    if os.path.exists(env):
        parts.append(md_s10(env))
    return "\n".join(p for p in parts if p)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "summary":
        print(summary(sys.argv[2]), end="")
    elif cmd == "table":
        print(table(sys.argv[2:]), end="")
    else:
        sys.exit("usage: report.py summary <results_dir> | table <run_dir>...")
