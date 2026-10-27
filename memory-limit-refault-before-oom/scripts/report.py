import glob
import json
import os
import sys

R = sys.argv[1]
S3 = os.path.join(R, "s3")


def load(job, run):
    p = os.path.join(S3, job, run, "summary.json")
    return json.load(open(p)) if os.path.exists(p) else None


def p1_run():
    p = os.path.join(S3, "p1", "p1_final.txt")
    if not os.path.exists(p):
        return "p1"
    return dict(l.strip().split("=", 1) for l in open(p) if "=" in l).get("run", "p1")


def fmt(v, n=None):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if n is not None and isinstance(v, (int, float)):
        return f"{v:.{n}f}"
    return str(v)


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(fmt(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def w(o, k):
    return (o.get("window") or {}).get(k)


P1 = p1_run()
lines = ["# memory-limit-refault-before-oom", ""]

lines += ["## Section 3 (verification)", "", "### Baseline, N1, P1, P2, F1, R1", ""]
rows = []
for name, job, run in [("baseline", "p1", "base"), ("N1", "n1", "n1"), ("P1", "p1", P1), ("P2", "p2", "p2"), ("F1", "f1", "f1"), ("R1", "r1", "r1")]:
    o = load(job, run)
    if not o:
        continue
    rows.append([name, f"s3-{job}", fmt(o["limit_mib"], 2), o["latency_ms"]["p99"], o["timeouts"],
                 fmt(o['headroom_mib']['median'], 2), o["anon_pct_of_limit"], o["file_pct_of_limit"],
                 w(o, "mem_psi_some_s_per_s"), w(o, "io_psi_some_s_per_s"), w(o, "pgmajfault_per_s"), w(o, "pgscan_direct_per_s"),
                 w(o, "pgsteal_direct_per_s"), w(o, "workingset_refault_file_per_s"), w(o, "events_max_per_s"),
                 f"{fmt(w(o, 'cpu_user_s_per_s'), 3)} / {fmt(w(o, 'cpu_system_s_per_s'), 3)}", o["oom_killed"],
                 o.get("read_ahead_kb"), w(o, "workingset_refault_file_per_pgmajfault"), w(o, "io_psi_some_us_per_pgmajfault")])
lines += [table(["condition", "job", "memory.max (MiB)", "p99 (ms)", "timeouts", "headroom (MiB)", "anon (% of limit)", "file (% of limit)",
                 "memory PSI some (s/s)", "I/O PSI some (s/s)", "pgmajfault (/s)", "pgscan_direct (/s)", "pgsteal_direct (/s)",
                 "workingset_refault_file (/s)", "memory.events max (/s)", "CPU user / system (s/s)", "OOM kill",
                 "read_ahead_kb", "workingset_refault_file per pgmajfault", "I/O PSI some per pgmajfault (us)"], rows), ""]
o = load("p1", P1)
if o:
    lines += [f"Each condition ran {o['n_requests']} requests ({fmt(o['rps'], 0)}/s for {o['dur_s']} s). Medians of 1 s samples; rates over the load window.", ""]

p3 = load("p3", "p3")
if p3:
    x = p3["p3"]
    lines += ["### P3 time series (job s3-p3)", "",
              table(["t (s)", "p99 (ms)", "completed (/s)", "anon (MiB)", "file (MiB)", "headroom (MiB)", "memory PSI some (s/s)", "pgmajfault (/s)", "oom_kill"],
                    [[r["t_s"], r["p99_ms"], r.get("completed_per_s"), r.get("anon_mib"), r.get("file_mib"), r.get("headroom_mib"), r.get("mem_psi_some_s_per_s"), r.get("pgmajfault_per_s"), r.get("oom_kill")] for r in x["rows_5s"]]), ""]
    end = f"OOM kill at {fmt(x['oom_kill_at_s'])} s" if x["oom_kill_at_s"] is not None else "no OOM kill; cut off at 180 s"
    first = (f"pgmajfault first exceeded 100/s at {fmt(x['first_pgmajfault_over_100_s'])} s, headroom {fmt(x['headroom_at_edge_mib'])} MiB, file_mapped {fmt(x['file_mapped_at_edge_mib'])} MiB"
             if x["first_pgmajfault_over_100_s"] is not None else "pgmajfault never exceeded 100/s")
    lines += [f"{first}; {end}. p99 of the first 10 s {fmt(x['baseline_p99_ms_first10s'])} ms. Growth {fmt(p3['grow_kbps'])} KiB/s requested, anon grew {fmt(x['anon_growth_mib_per_s'])} MiB/s.", ""]

lines += ["## Section 4 (telling it apart)", ""]
rows = []
for name, job, run in [("N1", "n1", "n1"), ("P1", "p1", P1)]:
    o = load(job, run)
    if o:
        rows.append([name, f"s3-{job}", o["usage_pct_of_limit"], w(o, "pgmajfault_per_s"), w(o, "mem_psi_some_s_per_s")])
lines += [table(["condition", "job", "memory.current (% of limit)", "pgmajfault (/s)", "memory PSI some (s/s)"], rows), ""]

lines += ["## Section 5 (fixes)", ""]
rows = []
for name, job, run in [("baseline", "p1", "base"), ("P1", "p1", P1), ("F1", "f1", "f1")]:
    o = load(job, run)
    if o:
        rows.append([name, f"s3-{job}", fmt(o["limit_mib"], 2), fmt(o["cache_mib"], 1), o["latency_ms"]["p99"]])
lines += [table(["condition", "job", "--memory (MiB)", "in-process cache (MiB)", "p99 (ms)"], rows), ""]

lines += ["## Numbers not for the article", ""]
tries = os.path.join(S3, "p1", "p1_tries.txt")
if os.path.exists(tries):
    lines += ["### P1 headroom (job s3-p1)", ""] + [f"- {l.strip()}" for l in open(tries)] + [""]
swing = [json.load(open(f))["nonfile_mib"]["swing"] for f in glob.glob(os.path.join(S3, "*", "base", "summary.json"))]
o = load("p1", P1)
if swing and o:
    lines += [f"Gate: largest 1 s swing of memory.current - file in the baselines {fmt(max(swing))} MiB; P1 headroom {fmt(o['headroom_mib']['median'])} MiB >= 4x swing: {fmt(o['headroom_mib']['median'] >= 4 * max(swing))}.", ""]
lines += ["### Baseline of each job", ""]
rows = []
for d in sorted(glob.glob(os.path.join(S3, "*", "base", "summary.json"))):
    o = json.load(open(d))
    job = os.path.basename(os.path.dirname(os.path.dirname(d)))
    sm = o.get("smaps") or {}
    rows.append([f"s3-{job}", fmt(o["limit_mib"], 2), o["latency_ms"]["p50"], o["latency_ms"]["p99"], o["timeouts"], o["headroom_mib"]["median"], o["anon_mib"], o["file_mib"], o["file_mapped_mib"],
                 w(o, "pgmajfault_per_s"), w(o, "events_max_per_s"), o["anon_slope_mib_per_s"], o["nonfile_mib"]["swing"], sm.get("touched_kb"), sm.get("referenced_kb"), sm.get("exec_rss_kb")])
lines += [table(["job", "memory.max (MiB)", "p50 (ms)", "p99 (ms)", "timeouts", "headroom (MiB)", "anon (MiB)", "file (MiB)", "file_mapped (MiB)", "pgmajfault (/s)",
                 "memory.events max (/s)", "anon slope (MiB/s)", "memory.current - file swing (MiB)", "Referenced - Anonymous (KiB)", "Referenced (KiB)", "executable Rss (KiB)"], rows), ""]

lines += ["### Environment", ""]
envs = sorted(glob.glob(os.path.join(R, "environment", "*.txt")))
for e in envs:
    kv = dict(l.rstrip("\n").split("=", 1) for l in open(e) if "=" in l)
    lines.append(f"- {os.path.basename(e)[:-4]}: kernel {kv.get('kernel')}, {kv.get('os')}, CPU {kv.get('cpu_model') or '-'} x{kv.get('nproc')}, lru_gen/enabled {kv.get('lru_gen_enabled')}, read_ahead_kb {kv.get('read_ahead_kb')} ({kv.get('read_ahead_kb_path')}), swapon [{kv.get('swapon')}], Docker {kv.get('docker')} ({kv.get('docker_cgroup_driver')}) on {kv.get('docker_root_device')}")
if envs:
    kv = dict(l.rstrip("\n").split("=", 1) for l in open(envs[0]) if "=" in l)
    lines.append(f"- load and server: rps={kv.get('rps')}")
for f in sorted(glob.glob(os.path.join(S3, "*", "read_ahead_switch.txt"))):
    lines.append(f"- read_ahead_kb switch (s3-{os.path.basename(os.path.dirname(f))}): {open(f).read().strip().replace(chr(10), ', ')}")
for f in sorted(glob.glob(os.path.join(S3, "*", "lru_switch.txt"))):
    lines.append(f"- LRU switch (s3-{os.path.basename(os.path.dirname(f))}): {open(f).read().strip().replace(chr(10), ', ')}")
o = load("p1", "base")
if o and o.get("app"):
    a = o["app"]
    lines.append(f"- python {a['python']}; " + ", ".join(f"{k} {v}" for k, v in a["packages"].items()))
    lines.append(f"- loop {a['loop']}; HTTP {', '.join(a['http_modules'])}; BLAS {a['numpy_config'].get('name')} {a['numpy_config'].get('version')}; OPENBLAS_NUM_THREADS={a['openblas_num_threads']}")
    lines.append(f"- per request: numpy {a['work_n']}x{a['work_n']} matmul, eigvalsh, rfft, sort; added native code: {', '.join(a['extra']) or 'none'}; cache element {a['elem_bytes'] // 1024} KiB")
swaps = sorted({(o2['swap']['max'], o2['swap']['current_max']) for o2 in (json.load(open(f)) for f in glob.glob(os.path.join(S3, '*', '*', 'summary.json'))) if 'swap' in o2})
lines.append(f"- memory.swap.max / largest memory.swap.current in all runs: {', '.join(f'{a} / {b}' for a, b in swaps)}")
times = []
for f in sorted(glob.glob(os.path.join(S3, "*", "job_seconds.txt"))):
    times.append(f"s3-{os.path.basename(os.path.dirname(f))} {open(f).read().strip()} s")
if times:
    lines.append("- job durations: " + ", ".join(times))
print("\n".join(lines))
