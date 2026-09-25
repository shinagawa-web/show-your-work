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
    kc = c["n_ge1"]
    s = md_rows("Section 6 (mechanism)", throttle_rows(o) + [
        ("throttled_usec / nr_throttled (us)", o["throttled_usec_per_nr_throttled"]),
        ("requests crossing at least one throttle (kprobe)", f"{kc} of {c['n']}" if isinstance(kc, int) else kc),
        ("requests with nr_throttled increasing while in flight (10ms cpu.stat)", f"{c['n_ge1_nr_throttled_10ms']} of {c['n']}"),
    ])
    s += "\n" + md_rows("Requests at or above p99", [(r["lat_ms"], r["kprobe_crossings"], r["nr_throttled_delta_10ms"], r["recvq_at_send"]) for r in o["slow_ge_p99"]],
                        head=("latency (ms)", "throttles crossed (kprobe)", "nr_throttled increase (10ms cpu.stat)", "Recv-Q at send"))
    tl = o.get("tid_link")
    if isinstance(tl, dict):
        q = tl["cg_oncpu_period_start_to_throttle_ms"] or {}
        s += "\n" + md_rows("Throttles linked to the handling thread (bpftrace)", [
            ("requests linked to a server thread (ambiguous port match)", f"{tl['n_requests_linked']} of {tl['n']} ({tl['n_link_ambiguous']})"),
            ("requests with at least one throttle stopping the handling thread", f"{tl['n_ge1_stopping_tid']} of {tl['n']}"),
            ("requests with a throttle while waiting before accept", f"{tl['n_ge1_before_accept']} of {tl['n']}"),
            ("requests whose count differs from the time-overlap count", f"{tl['n_differs_from_time_overlap']} {tl['differs_by']}"),
            ("same, counting throttles before accept too", tl["n_differs_counting_before_accept_union"]),
            ("stage of each throttle overlapping a request, all requests", tl.get("throttle_stages_all_requests")),
            ("quota per period (ms)", tl["quota_ms"]),
            ("cgroup on-CPU from period start to throttle, min / median / max (ms)", f"{q.get('min')} / {q.get('p50')} / {q.get('max')} ({q.get('n')} throttles)"),
            ("quota usage source", tl["quota_usage_source"]),
        ])

        def segs(r):
            return "; ".join(f"+{g['begin_rel_period_start_ms']} ms into period, cgroup used {g['cg_oncpu_since_period_start_ms']} ms, "
                             f"on-CPU {g['req_oncpu_ms']} of {g['len_ms']} ms, {g['cg_threads_active']} threads, then {g['ends_with']}" for g in r["segments"])

        def thr(r):
            return "; ".join(f"{t['tid_state']}{' (tid was running)' if t['req_tid_was_running'] else ''}, {t['len_ms']} ms" for t in r["throttles"])

        rows = [(r["lat_ms"], r["tid"], ", ".join(r.get("throttle_stages", [])), r["client_to_accept_ms"], r["throttles_before_accept"], f"{r['throttles_stopping_tid']} / {r['throttles_overlapping_server_span']}",
                 r["throttles_time_overlap_old"], thr(r), segs(r)) for r in o["slow_ge_p99_tid_link"] if r.get("linked")]
        s += "\n" + md_rows("Requests at or above p99, linked by thread", rows,
                            head=("latency (ms)", "tid", "throttle stages", "send to accept (ms)", "throttles before accept", "throttles stopping tid / in server span",
                                  "throttles (time overlap)", "tid state at each throttle", "segments between throttles"))
    s += "\n" + md_rows("Latency histogram (10ms bins)", [(f"{b}-{int(b) + 10}", v) for b, v in o["hist_latency_10ms"].items()], head=("latency (ms)", "requests"))
    return s


def throttle_rows(o):
    k = o["kprobe"]
    if not isinstance(k, dict):
        return [("throttle length (kprobe)", k)]
    u, pc = k["union_len_ms"] or {}, k["per_cpu_len_ms"] or {}

    def ends(e):
        if not e:
            return None
        m = e["unthrottle_minus_next_timer_ms"] or {}
        return (f"{e['n_ended_by_next_timer_plus_1ms']} of {e['n']} (ended before the timer: {e['n_ended_before_next_timer']}; "
                f"unthrottle minus timer min / median / max {m.get('min')} / {m.get('p50')} / {m.get('max')} ms)")

    return [
        ("throttle length (kprobe), union of CPUs, median / mean / max (ms)", f"{u.get('p50')} / {u.get('mean')} / {u.get('max')} ({k['throttle_windows_in_window_union']} throttles)"),
        ("throttle length (kprobe), per CPU, median / mean / max (ms)", f"{pc.get('p50')} / {pc.get('mean')} / {pc.get('max')} ({k['throttle_events_in_window_per_cpu']} events)"),
        ("sum of throttle lengths, per CPU / union (ms)", f"{pc.get('sum')} / {u.get('sum')}"),
        ("throttled_usec increase over the run (ms)", k.get("throttled_usec_delta_ms")),
        ("windows used for the two sums", k.get("len_sum_window")),
        ("throttles ended by the next period timer (+1ms), per CPU", ends(k.get("ends_by_next_period_timer_per_cpu"))),
        ("throttles ended by the next period timer (+1ms), union", ends(k.get("ends_by_next_period_timer_union"))),
    ]


PERIOD_RUNS = ["s3", "s5/rps10_w1"]


def md_periods(d, title):
    o = load(d)
    pa, pu = o.get("period_arrivals"), o.get("period_usage")
    if not isinstance(pa, dict) or not isinstance(pu, dict):
        return None
    parts = [] if os.path.basename(d.rstrip("/")) == "s3" else [md_rows(f"{title}: throttle", throttle_rows(o))]
    parts.append(md_rows(f"{title}: periods", [
        ("periods (start at a real timer firing)", f"{pa['periods']} ({pa['periods_real_timer']})"),
        ("throttle calls / throttled periods", f"{pa['throttle_calls']} / {pa['throttled_periods']}"),
        ("requests / linked to a thread", f"{pa['requests']} / {pa['requests_linked']}"),
        ("carry-over threshold (ms)", pa["carry_threshold_ms"]),
    ]))
    head = ("bin", "periods", "throttled", "fraction")
    for key, t in (("by_arrivals_all", "all periods, by arrivals (send time)"),
                   ("by_arrivals_no_carry", "no carry-over, by arrivals"),
                   ("by_arrivals_carry", "carry-over, by arrivals"),
                   ("by_demand_in_period_ms", "in-period demand (ms): sum(min(20, time left)) + carry"),
                   ("by_cgroup_oncpu_ms", "cgroup on-CPU in the period (ms, sched_switch)")):
        parts.append(md_rows(f"{title}: {t}", [(r["bin"], r["periods"], r["throttled"], r["fraction"]) for r in pa[key]], head=head))
    parts.append(md_rows(f"{title}: arrivals in throttled periods", [(r["arrivals"], r["all"], r["no_carry"], r["carry"]) for r in pa["arrivals_in_throttled_periods"]],
                         head=("arrivals", "all", "no carry-over", "carry-over")))
    parts.append(md_rows(f"{title}: 3+ arrivals, no carry-over, not throttled", [(r["k"], r["arrivals"], r["offsets_ms"], r["cg_ms"], r["next_period_throttled"]) for r in pa["ge3_arrivals_no_carry_not_throttled"]],
                         head=("period", "arrivals", "send offsets in period (ms)", "cgroup on-CPU (ms)", "next period throttled")))
    keys = ["windows", "usage_ge_near", "dthr_gt0", "both", "p_usage_ge_near_given_dthr", "p_dthr_given_usage_ge_near", "max_usage_ms",
            "usage_sample_minus_boundary_ms_median", "nr_throttled_sample_minus_boundary_ms_median"]
    rows = [("aligned",) + tuple(pu["aligned"][x] for x in keys), (f"naive (+{pu['naive_phase_ms']} ms)",) + tuple(pu["naive"][x] for x in keys)]
    rows += [(f"offset +{x['offset_ms']} ms",) + tuple(x[y] for y in keys) for x in pu["offset_sweep"]]
    parts.append(md_rows(f"{title}: cpu.stat usage in 100ms windows vs nr_throttled (near = {pu['near_ms']} ms)", rows, head=("window set",) + tuple(keys)))
    for name in ("aligned", "naive"):
        parts.append(md_rows(f"{title}: {name} windows by usage", [(r["bin"], r["windows"], r["dthr_gt0"], r["bpf_throttle"]) for r in pu[name]["by_usage_ms"]],
                             head=("usage (ms)", "windows", "nr_throttled increased", "bpftrace throttle")))
    return "\n".join(parts)


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
    for rel in PERIOD_RUNS:
        d = os.path.join(results, rel)
        if os.path.exists(os.path.join(d, "summary.json")):
            parts.append(md_periods(d, f"Per-period analysis, {os.path.basename(d)}"))
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
