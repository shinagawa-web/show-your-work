import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
wanted = sys.argv[2:] or ["s3", "s4", "s5"]
hosts = sorted(d for d in (root / "host").iterdir() if d.is_dir()) if (root / "host").exists() else []
conditions = [line.split("\t")[:2] for line in (Path(__file__).resolve().parent.parent / "conditions.tsv").read_text().splitlines()[1:]]


def load(sec, name):
    p = root / sec / name / "analysis.json"
    return json.loads(p.read_text()) if p.exists() else None


def setting(sec, name, key):
    for line in (root / sec / name / "settings.txt").read_text().splitlines():
        k, _, v = line.partition("\t")
        if k == key:
            return v
    return ""


def fio_arg(sec, name, key):
    for line in (root / sec / name / "fio_args.txt").read_text().splitlines():
        if line.startswith(f"--{key}="):
            return line.split("=", 1)[1]
    return ""


def limit(sec, name):
    r = setting(sec, name, "riops")
    return "none" if r == "max" else r


def issued(sec, name):
    eng, jobs, depth = setting(sec, name, "engine"), int(setting(sec, name, "numjobs")), int(setting(sec, name, "iodepth"))
    if setting(sec, name, "pattern") == "batch":
        return f"batches of {depth} reads ({eng}), then {int(fio_arg(sec, name, 'thinktime')) // 1000} ms idle"
    if eng == "psync":
        return f"{jobs} thread{'s' if jobs > 1 else ''}, each reads one at a time without pause"
    return f"{jobs} thread, {depth} reads in flight ({eng})"


def how_limited(sec, name):
    via, r = setting(sec, name, "via"), setting(sec, name, "riops")
    src = (root / sec / name / "limit_source.txt")
    src = src.read_text().splitlines()[0] if src.exists() else ""
    if via == "cgroup":
        return "a write to the io.max file of the cgroup of fio"
    if via == "systemd":
        return f"systemd-run with IOReadIOPSMax= given the device path (`{src}`)"
    if via == "docker":
        return f"docker run --device-read-iops (`{src}`)"
    return f"systemd-run with IOReadIOPSMax= given the path of a file on ext4 on dm-linear on the loop device (`{src}`)"


def load_desc(sec, name):
    return f"{setting(sec, name, 'numjobs')} threads, limit riops={setting(sec, name, 'riops')}"


def order(n):
    keys = ["base", "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9"]
    return next((i for i, k in enumerate(keys) if n.startswith(k)), 99), n


def names(sec):
    d = root / sec
    if not d.exists():
        return []
    return sorted((p.name for p in d.iterdir() if usable(load(sec, p.name))), key=order)


def usable(a):
    return bool(a) and a.get("p50_ms") is not None and bool(a.get("iostat"))


def f(x, nd=3):
    return "-" if x is None else f"{x:.{nd}f}"


def limited_dev(a):
    return next(k for k in a["iostat"] if k.startswith("loop"))


def lat_cells(a):
    return f"{f(a['p50_ms'])} | {f(a['p90_ms'])} | {f(a['p99_ms'])} | {f(a['mean_ms'])} | {f(a['stopped_pct'], 1)} | {a['pred_label_100']}"


def measured_slices():
    seen = []
    for sec in ("s3", "s4", "s5"):
        for n in names(sec):
            a = load(sec, n)
            if a.get("slice_ms_measured"):
                seen.append(f"{sec}/{n} {a['slice_ms_measured']}")
    return seen


def text(sec, name, fn):
    p = root / sec / name / fn
    return p.read_text().rstrip("\n").splitlines() if p.exists() else []


def runs(lines):
    out, cur = [], []
    for x in lines:
        if x.startswith("$ ") and cur:
            out.append(cur)
            cur = []
        cur.append(x)
    return out + ([cur] if cur else [])


def block(lines):
    return ["```"] + lines + ["```", ""]


def rios_of(cmd_run):
    for x in cmd_run[1:]:
        for kv in x.split()[1:]:
            k, _, v = kv.partition("=")
            if k == "rios":
                return int(v)
    return 0


out = ["# io-max-hidden-from-iostat", ""]
env = {}
for envf in (h / "environment.txt" for h in hosts):
    if not envf.exists():
        continue
    for line in envf.read_text().splitlines():
        if line.startswith("fio-"):
            env["fio"] = line
any_cond = next(((s, n) for s in ("s3", "s4", "s5") for n in names(s)), None)
if any_cond:
    s0, n0 = any_cond
    out.append(f"Load in every condition unless a section says otherwise: {env.get('fio', 'fio')}, 4 KiB random reads with O_DIRECT, ioengine psync, 16 threads that each issue one read at a time without pause, {fio_arg(s0, n0, 'runtime')} s per condition, on a loop device (direct I/O) backed by a file in /dev/shm. The limit is cgroup v2 io.max riops on that loop device.")
    out.append("")
out.append("stopped (%): share of reads in the fio latency histogram that took longer than 50 ms, half of the 100 ms throttle slice. predicted: concurrent reads / reads allowed per 100 ms slice (riops / 10); for batched reads, (batch - reads per slice) / batch.")
out.append("")

c3 = names("s3")
s3_rows = [n for n in c3 if n in ("base", "p1_run1") or n.startswith("p4")]
if s3_rows:
    out += ["## Section 3 (job s3)", ""]
    out.append("iostat -dxy 1, mean over 9 one-second samples, row of the loop device that has the limit (r_await weighted by r/s)")
    out.append("")
    out.append("| limit (riops) | how reads are issued | r/s | r_await (ms) | %util | condition |")
    out.append("|---|---|---|---|---|---|")
    for n in s3_rows:
        a = load("s3", n)
        v = a["iostat"][limited_dev(a)]
        out.append(f"| {limit('s3', n)} | {issued('s3', n)} | {f(v['r_s'], 1)} | {f(v['r_await_ms'])} | {f(v['util_pct'], 2)} | {n} |")
    out += ["", "fio read latency (lat)", ""]
    out.append("| limit (riops) | how reads are issued | p50 (ms) | p90 (ms) | p99 (ms) | mean (ms) | stopped (%) | predicted | condition |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for n in s3_rows:
        out.append(f"| {limit('s3', n)} | {issued('s3', n)} | {lat_cells(load('s3', n))} | {n} |")
    out.append("")

c4 = names("s4")

if c4:
    out += ["## Section 4 (job s4)", ""]
    for n in c4:
        if n.startswith(("p5", "p6")):
            out.append(f"{load_desc('s4', n)}, set with {how_limited('s4', n)} ({n}). The cgroup of one fio process (PID as seen from the host) and the io.max of that cgroup:")
            out.append("")
            out += block(text("s4", n, "proc_cgroup.txt"))
            out.append(f"The limit as held where it was set ({n}):")
            out.append("")
            out += block(text("s4", n, "limit_setting.txt"))
    p9 = next((n for n in c4 if n.startswith("p9")), None)
    if p9:
        a = load("s4", p9)
        out.append(f"{load_desc('s4', p9)}, set with {how_limited('s4', p9)} ({p9}). The io.max of the cgroup of fio, and the device names for the device numbers:")
        out.append("")
        out += block(runs(text("s4", p9, "proc_cgroup.txt"))[-1] + text("s4", p9, "lsblk.txt"))
        out.append(f"iostat rows during the same run ({p9}):")
        out.append("")
        out.append("| iostat row | r/s | r_await (ms) | %util |")
        out.append("|---|---|---|---|")
        for d, v in a["iostat"].items():
            out.append(f"| {d} | {f(v['r_s'], 1)} | {f(v['r_await_ms'])} | {f(v['util_pct'], 2)} |")
        out.append("")
    d = root / "s4" / "p1"
    if "p1" in c4 and (d / "ftrace_commands.txt").exists() and (d / "ftrace_extract.txt").exists():
        a = load("s4", "p1")
        out.append(f"{load_desc('s4', 'p1')}, set with {how_limited('s4', 'p1')} (p1). The device name for the device number:")
        out.append("")
        out += block(text("s4", "p1", "lsblk.txt"))
        out.append("While fio ran, the blk tracer in tracefs was enabled on the limited loop device as root; it writes a `throtl [R] bio.` line for each read held by io.max (p1):")
        out.append("")
        out += block(["$ " + x for x in text("s4", "p1", "ftrace_commands.txt")])
        out += block([x.strip() if not x.startswith("$") else x for x in text("s4", "p1", "ftrace_extract.txt")])
        out.append(f"- fio reads over 50 ms in the same run: {a['stopped_count']} of {a['hist_total']}")
        out.append("")
    d = root / "s4" / "p1_window"
    if "p1_window" in c4 and (d / "ftrace_commands.txt").exists() and (d / "io.stat_trace.txt").exists():
        cmds = ["$ " + x for x in text("s4", "p1_window", "ftrace_commands.txt")]
        cut = next(i for i, x in enumerate(cmds) if x.startswith("$ echo 0 >"))
        reads = runs(text("s4", "p1_window", "io.stat_trace.txt"))
        tm = dict(x.split("\t") for x in text("s4", "p1_window", "window_times.txt"))
        extract = text("s4", "p1_window", "ftrace_extract.txt")
        held = int(extract[-1].strip())
        out.append(f"{load_desc('s4', 'p1_window')}, set with {how_limited('s4', 'p1_window')}, fio reading for {fio_arg('s4', 'p1_window', 'runtime')} s. The blk tracer is started while fio is running, and the io.stat of the cgroup of fio is read right after. Seconds after fio started: tracer on at {tm.get('tracer on')}, io.stat read at {tm.get('first io.stat')} (p1_window):")
        out.append("")
        out += block(cmds[:cut] + reads[0])
        out.append(f"The tracer records while fio keeps reading. io.stat is read again right before the tracer is turned off. Seconds after fio started: io.stat read at {tm.get('second io.stat')}, tracer off at {tm.get('tracer off')} (p1_window):")
        out.append("")
        out += block((reads[1] if len(reads) > 1 else []) + cmds[cut:])
        out += block([x.strip() if not x.startswith("$") else x for x in extract[-2:]])
        if len(reads) > 1:
            r0, r1 = rios_of(reads[0]), rios_of(reads[1])
            share = f"{held / (r1 - r0) * 100:.1f}" if r1 > r0 else "-"
            out.append(f"- rios between the two io.stat reads: {r1} - {r0} = {r1 - r0}; `throtl [R] bio.` lines / that difference: {held} / {r1 - r0} = {share} %")
        out.append("")
    d = root / "s4" / "p1_blktrace"
    if "p1_blktrace" in c4 and (d / "blktrace_throtl_count.txt").exists():
        cmds = (d / "blktrace_commands.txt").read_text().strip().splitlines()
        cnt = (d / "blktrace_throtl_count.txt").read_text().strip()
        out.append(f"The same load and limit with the blktrace command at its defaults instead (`{cmds[0]}`, then `{cmds[1]}`): {cnt} (p1_blktrace)")
        out.append("")

c5 = names("s5")
if c5:
    out += ["## Section 5 (job s5)", ""]
    out.append("| concurrent reads | limit (riops) | reads allowed per 100 ms slice | p50 (ms) | p90 (ms) | p99 (ms) | mean (ms) | stopped (%) | predicted | condition |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for n in c5:
        a = load("s5", n)
        out.append(f"| {a['concurrent']} | {a['riops']} | {int(a['riops']) * a['stop_slice_ms'] // 1000} | {lat_cells(a)} | {n} |")
    out.append("")

out += ["## Not used for numbers", ""]
p8 = [(n, load("s3", n)) for n in c3 if n.startswith("p8")]
if p8:
    out.append("- slice is 100 ms for rotational 0 and 1 (`throtl [R] new slice start= end=`, (end - start) / CONFIG_HZ): " + "; ".join(f"{n} rotational={setting('s3', n, 'rotational_at_first_iomax')} slice {a.get('slice_ms_measured')} ms, stopped {f(a['stopped_pct'], 1)} %, max {f(a['max_ms'])} ms" for n, a in p8))
ms = [x for x in measured_slices() if not x.startswith("s3/p8")]
if ms:
    out.append("- slice from the same lines in the other traced conditions: " + "; ".join(ms))
pp = [("base", load("s3", "base") if "base" in c3 else None), ("p1 (psync)", load("s4", "p1") if "p1" in c4 else None)]
p7 = next((n for n in c4 if n.startswith("p7")), None)
if p7:
    pp.append(("p7 (libaio)", load("s4", p7)))
pp = [(n, a) for n, a in pp if a]
if pp:
    out.append("- io.pressure some (% of 9 s) and pidstat -d iodelay (sum over fio processes, Average): " + "; ".join(f"{n} {f(a['io_pressure_some_pct'], 1)} % / {a['iodelay_ticks_per_s_sum']}" for n, a in pp))
rot = []
for sec in ("s3", "s4", "s5"):
    for n in names(sec):
        st = (root / sec / n / "settings.txt").read_text().splitlines()
        rot.append(f"{sec}/{n} " + ",".join(x.split("\t")[0].removeprefix("rotational_") + "=" + x.split("\t")[1] for x in st if x.startswith("rotational_") and x.split("\t")[1]))
if rot:
    out.append("- rotational per condition: " + "; ".join(rot))
p1 = [load("s3", n) for n in c3 if n.startswith("p1_run")]
if p1:
    def rng(k, nd=3):
        v = [a[k] for a in p1]
        return f"{min(v):.{nd}f} to {max(v):.{nd}f}"
    out.append(f"- P1 x{len(p1)} (s3): p50 {rng('p50_ms')} ms, p90 {rng('p90_ms')} ms, p99 {rng('p99_ms')} ms, mean {rng('mean_ms')} ms, stopped {rng('stopped_pct', 1)} %")
for n in c3:
    a = load("s3", n)
    if "batches" in a:
        b = a["batches"][:-1] if len(a["batches"]) > 1 else a["batches"]
        def span(k):
            v = [x[k] for x in b if x.get(k) is not None]
            return f"{min(v)} to {max(v)}" if v else "-"
        out.append(f"- s3/{n} batches: {len(a['batches'])}, reads per batch {sorted(set(x['reads'] for x in b))}, held per batch {sorted(set(x['held'] for x in b))}, first to last submission {span('submit_span_ms')} ms (a held read counts at its `[R] bio.` line, since Q is traced after the throttle), first submission to last completion {span('last_done_ms')} ms, idle after last completion {span('idle_ms_to_next')} ms, mean rios/s {a['io_stat_rios_per_s']:.0f}")
flat = []
for sec in ("s3", "s4", "s5"):
    for n in names(sec):
        a = load(sec, n)
        each = a.get("io_stat_rios_per_s_each", [])
        if each and a["riops"] != "max":
            flat.append(f"{sec}/{n} {min(each)}-{max(each)}")
if flat:
    out.append("- io.stat rios per second, min-max over 9 s, limited conditions: " + "; ".join(flat))
for sec in ("s3", "s4", "s5"):
    for n in names(sec):
        a = load(sec, n)
        if a.get("ftrace_overrun") and a["ftrace_overrun"] != "overrun 0":
            out.append(f"- {sec}/{n} ftrace {a['ftrace_overrun']}")
for h in hosts:
    tim = h / "timing.tsv"
    if tim.exists():
        out.append(f"- seconds ({h.name}): " + ", ".join(f"{r[0]} {r[2]}" for r in (ln.split("\t") for ln in tim.read_text().splitlines()[1:]) if r[1] == "total"))
    env = h / "environment.txt"
    if env.exists():
        out.append(f"- environment ({h.name}): " + "; ".join(env.read_text().splitlines()))

invalid = []
for sec, n in conditions:
    if not any(w == sec or w == f"{sec}:{n}" for w in wanted):
        continue
    a = load(sec, n)
    if a is None:
        invalid.append(f"RUN INVALID: {sec}/{n}: no results/{sec}/{n}/analysis.json")
    elif a["invalid"] or not usable(a):
        invalid.append(f"RUN INVALID: {sec}/{n}: {'; '.join(a['invalid']) or 'no fio or iostat values in analysis.json'}")
if invalid:
    out[2:2] = invalid + [""]
print("\n".join(out))
sys.exit(1 if invalid else 0)
