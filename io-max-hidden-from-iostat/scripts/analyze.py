import json
import statistics
import sys
from pathlib import Path

out = Path(sys.argv[1])


def read(name, default=""):
    p = out / name
    return p.read_text() if p.exists() else default


s = dict(line.split("\t", 1) for line in read("settings.txt").splitlines() if "\t" in line)
slice_ms = int(s["stop_slice_ms"])
hz = int(s.get("hz", "0") or 0)
riops = s["riops"]
c = int(s["numjobs"]) * int(s["iodepth"])
invalid = []

raw = read("fio.json")
fio = json.loads(raw[raw.find("{"):]) if "{" in raw else None
res = {"name": s["name"], "stop_slice_ms": slice_ms, "concurrent": c, "riops": riops}
if fio is None:
    invalid.append("no fio json")
else:
    rd = fio["jobs"][0]["read"]
    lat = rd["lat_ns"]
    pct = lat.get("percentile", {})
    for k, label in (("50.000000", "p50"), ("90.000000", "p90"), ("99.000000", "p99"), ("99.900000", "p99_9")):
        res[f"{label}_ms"] = pct[k] / 1e6 if k in pct else None
    res["mean_ms"] = lat["mean"] / 1e6
    res["max_ms"] = lat["max"] / 1e6
    res["ios"] = rd["total_ios"]
    res["fio_iops"] = rd["iops"]
    bins_src = "lat_ns" if "bins" in lat else "clat_ns"
    bins = {int(k): v for k, v in rd[bins_src]["bins"].items()}
    total = sum(bins.values())
    res["hist_source"] = bins_src
    res["hist_total"] = total
    thr = slice_ms * 1e6 / 2
    stopped = sum(v for k, v in bins.items() if k > thr)
    res["stopped_threshold_ms"] = slice_ms / 2
    res["stopped_count"] = stopped
    res["stopped_pct"] = 100 * stopped / total if total else None
    for ms in (1, 10, 50):
        res[f"over_{ms}ms_pct"] = 100 * sum(v for k, v in bins.items() if k > ms * 1e6) / total if total else None
    above = sorted((k, v) for k, v in bins.items() if k > thr)
    if above:
        half, acc = stopped / 2, 0
        for k, v in above:
            acc += v
            if acc >= half:
                res["stopped_median_ms"] = k / 1e6
                break
    if total == 0:
        invalid.append("fio completed no reads")
if s.get("fio_rc", "0").strip() != "0":
    invalid.append(f"fio exit code {s.get('fio_rc')}")



def predict(sl):
    if riops == "max":
        return 0.0, "0"
    n = int(riops) * sl / 1000
    if s["pattern"] == "batch":
        p = max(0.0, (c - n) / c * 100)
        return p, (f"{p:.0f}% ({c - n:.0f} of {c})" if c > n else "0")
    if c < n:
        return c / n * 100, f"{c / n * 100:.0f}% ({c} of {n:.0f})"
    return None, f"almost all ({c} >= {n:.0f})"


for sl in (100, 20):
    res[f"pred_pct_{sl}"], res[f"pred_label_{sl}"] = predict(sl)
if riops != "max":
    if s["pattern"] != "batch":
        res["pred_mean_ms"] = c / int(riops) * 1000
    iomax = read("io.max.txt")
    if f"riops={riops}" not in iomax:
        invalid.append(f"io.max does not contain riops={riops}: {iomax.strip()!r}")
res["io_max"] = read("io.max.txt").strip()
res["cgroup"] = read("cgroup_path.txt").strip()

rows, header = {}, None
for line in read("iostat.txt").splitlines():
    f = line.split()
    if not f:
        continue
    if f[0] == "Device":
        header = f
        continue
    if header and len(f) == len(header):
        rows.setdefault(f[0], []).append(dict(zip(header[1:], map(float, f[1:]))))
res["iostat"] = {}
for dev, samples in rows.items():
    rs = [x["r/s"] for x in samples]
    w = sum(rs)
    res["iostat"][dev] = {
        "samples": len(samples),
        "r_s": statistics.mean(rs),
        "r_s_min": min(rs),
        "r_s_max": max(rs),
        "r_await_ms": sum(x["r/s"] * x["r_await"] for x in samples) / w if w else 0.0,
        "aqu_sz": statistics.mean(x["aqu-sz"] for x in samples),
        "util_pct": statistics.mean(x["%util"] for x in samples),
    }
if not rows:
    invalid.append("no iostat rows")

cg = [line.split("\t") for line in read("cg_samples.tsv").splitlines()[1:] if line.strip()]
if len(cg) >= 2:
    t = [float(x[0]) for x in cg]
    r = [int(x[1]) for x in cg]
    some = [int(x[2]) for x in cg]
    dt = t[-1] - t[0]
    res["io_stat_rios_per_s"] = (r[-1] - r[0]) / dt
    res["io_stat_rios_per_s_each"] = [r[i + 1] - r[i] for i in range(len(r) - 1)]
    res["io_pressure_some_pct"] = (some[-1] - some[0]) / (dt * 1e6) * 100
else:
    invalid.append("no cgroup samples")
res["io_pressure_end"] = read("io.pressure.txt").strip()

hdr, delays = None, []
for line in read("pidstat.txt").splitlines():
    f = line.split()
    if "iodelay" in f:
        hdr = f
        continue
    if hdr and f and f[0] == "Average:" and f[-1] == "fio":
        delays.append(int(f[hdr.index("iodelay")]))
res["pidstat_fio_tasks"] = len(delays)
res["iodelay_ticks_per_s_sum"] = sum(delays)

def ev_time(line):
    for tok in line.split():
        if tok.endswith(":") and tok[:-1].replace(".", "", 1).isdigit():
            return float(tok[:-1])
    return float(line.split()[3])


if s.get("trace") in ("tool", "ftrace"):
    res["trace"] = s["trace"]
    if (out / "blktrace_actions.txt").exists():
        res["blktrace_actions"] = read("blktrace_actions.txt").split("\n")
    res["ftrace_overrun"] = read("ftrace_stats.txt").strip()
    lines = read("throtl.txt").splitlines()
    slices = []
    for x in lines:
        if "new slice start=" in x:
            kv = dict(p.split("=") for p in x.split() if "=" in p)
            slices.append(int(kv["end"]) - int(kv["start"]))
    res["new_slice_lines"] = len(slices)
    res["slice_jiffies"] = sorted(set(slices))
    res["slice_ms_measured"] = sorted(set(j * 1000 / hz for j in slices)) if hz else None
    res["new_slice_example"] = next((x.strip() for x in lines if "new slice start=" in x), "")
    if s["trace"] == "tool" and not res.get("blktrace_actions", [""])[0]:
        invalid.append("blkparse produced no events")
    if s["trace"] == "ftrace":
        if not (out / "qc_times.txt").exists() or not read("qc_times.txt").strip():
            invalid.append("the blk tracer recorded no Q or C events")
        if not hz:
            invalid.append("CONFIG_HZ not found in /boot/config-$(uname -r), so the slice length is unknown")
        if riops != "max" and not slices:
            invalid.append("no `throtl [R] new slice` line from the blk tracer")
        if hz and any(j * 1000 / hz != slice_ms for j in slices):
            invalid.append(f"throttle slice {res['slice_ms_measured']} ms, but stopped and predicted assume {slice_ms} ms")
    queued = [x for x in lines if "[R] bio." in x]
    res["throtl_lines"] = len(lines)
    res["throtl_queued_lines"] = len(queued)
    res["throtl_examples"] = [x.strip() for x in queued[:2]]
    qc = [x.split() for x in read("qc_times.txt").splitlines() if x.strip()]
    q = sorted(float(x[0]) for x in qc if x[1] == "Q")
    cdone = sorted(float(x[0]) for x in qc if x[1] == "C")
    held = sorted(ev_time(x) for x in queued)
    res["q_events"] = len(q)
    res["c_events"] = len(cdone)
    if s["pattern"] == "batch":
        events = sorted([(x, "Q") for x in q] + [(x, "C") for x in cdone] + [(x, "H") for x in held])
        periods = []
        for ts, kind in events:
            if not periods or ts - periods[-1][-1][0] > 0.15:
                periods.append([])
            periods[-1].append((ts, kind))
        rows = []
        for i, per in enumerate(periods):
            pq = [ts for ts, k in per if k == "Q"]
            ph = [ts for ts, k in per if k == "H"]
            pc = [ts for ts, k in per if k == "C"]
            first = per[0][0]
            passed = len(pq) - len(ph)
            cands = ([pq[passed - 1]] if passed > 0 else []) + ph[-1:]
            last_submit = max(cands) if cands else first
            row = {"start_s": round(first, 4), "reads": len(pq), "held": len(ph),
                   "submit_span_ms": round((last_submit - first) * 1000, 3),
                   "last_done_ms": round((pc[-1] - first) * 1000, 2) if pc else None}
            if i + 1 < len(periods) and pc:
                row["idle_ms_to_next"] = round((periods[i + 1][0][0] - pc[-1]) * 1000, 1)
            rows.append(row)
        res["batches"] = rows

res["invalid"] = invalid
(out / "analysis.json").write_text(json.dumps(res, indent=1))
for k in ("p50_ms", "p90_ms", "p99_ms", "mean_ms", "max_ms", "stopped_pct", "pred_label_100", "pred_label_20", "slice_ms_measured", "io_stat_rios_per_s", "io_pressure_some_pct", "iodelay_ticks_per_s_sum", "throtl_queued_lines", "stopped_count"):
    if k in res:
        print(f"{k}\t{res[k]}")
for dev, v in res["iostat"].items():
    print(f"iostat {dev}\tr/s={v['r_s']:.1f} r_await={v['r_await_ms']:.3f} aqu-sz={v['aqu_sz']:.2f} %util={v['util_pct']:.2f}")
for m in invalid:
    print(f"RUN INVALID: {m}")
