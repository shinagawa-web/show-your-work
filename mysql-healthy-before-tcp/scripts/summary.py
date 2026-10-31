import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

results = Path(sys.argv[1])
jobs = sys.argv[2:]
table = [line.split("\t") for line in (Path(__file__).parent.parent / "jobs.tsv").read_text().splitlines()[1:]]
job_conds = {j: c.split() for j, c in table}
jobs = jobs or [j for j, _ in table]
conds = [c for j in jobs for c in job_conds[j]]
invalid = []

READY = re.compile(r"^(\S+Z) .*/mysqld: ready for connections\..* port: (\d+) ")
SHUTDOWN = re.compile(r"^(\S+Z) .*/mysqld: Shutdown complete")


def ts(s):
    s = s.rstrip("Z")
    head, _, frac = s.partition(".")
    dt = datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.timestamp() + float("0." + (frac or "0"))


def read_tests(path):
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            parts = line.split("\t", 3)
            if len(parts) == 4:
                rows.append((float(parts[0]), int(parts[2]), parts[3], float(parts[1])))
    return rows


def parse_run(d):
    r = {"dir": d}
    insp = json.loads((d / "inspect.json").read_text())[0]
    r["started"] = ts(insp["State"]["StartedAt"])
    r["hc"] = insp["Config"].get("Healthcheck") or {}
    r["port0"] = r["port3306"] = r["shutdown"] = None
    r["lines"] = {}
    for line in (d / "mysql.log").read_text(errors="replace").splitlines():
        m = READY.match(line)
        if m:
            key = "port0" if m.group(2) == "0" else "port3306" if m.group(2) == "3306" else None
            if key and r[key] is None:
                r[key] = ts(m.group(1))
                r["lines"][key] = line
        m = SHUTDOWN.match(line)
        if m and r["shutdown"] is None:
            r["shutdown"] = ts(m.group(1))
            r["lines"]["shutdown"] = line
    r["healthy"] = None
    for line in (d / "events.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e.get("Action") == "health_status: healthy":
            r["healthy"] = e["timeNano"] / 1e9
            break
    r["healthy_line"] = next((l for l in (d / "health_status.txt").read_text().splitlines() if "health_status: healthy" in l), None)
    r["tests"] = {v: read_tests(d / f"{v}.tsv") for v in ("net", "host")}
    if r["port3306"] is None or r["healthy"] is None:
        invalid.append(f"{d.relative_to(results)}: missing " + ", ".join(k for k in ("port3306", "healthy") if r[k] is None))
    for v, rows in r["tests"].items():
        if not rows:
            invalid.append(f"{d.relative_to(results)}: no {v} test output")
    if (d / "invalid.txt").exists():
        invalid.append(f"{d.relative_to(results)}: " + (d / "invalid.txt").read_text().strip())
    return r


def el(r, t):
    return "" if t is None else f"{t - r['started']:.2f}"


def hc_text(hc):
    test = hc.get("Test", [])
    cmd = test[1] if test[:1] == ["CMD-SHELL"] else " ".join(test[1:])
    parts = [f"`{cmd}`"]
    for k, name in (("Interval", "interval"), ("StartPeriod", "start_period"), ("StartInterval", "start_interval")):
        parts.append(f"{name} {hc[k] / 1e9:g}s" if hc.get(k) else f"{name} not set")
    return ", ".join(parts)


def env_of(c):
    env = {}
    for line in (Path(__file__).parent.parent / "conditions" / f"{c}.env").read_text().splitlines():
        k, _, v = line.partition("=")
        env[k] = v
    return env


def job_of(c):
    return (results / c / "job.txt").read_text().strip()


def outcomes(c):
    return [(r, v, r["tests"][v]) for r in runs.get(c, []) for v in ("net", "host") if r["tests"][v]]


def code_list(cmds):
    names = [f"`{x}`" for x in cmds]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def changed(c):
    parts = []
    yml = Path(__file__).parent.parent / "conditions" / f"{c}.yml"
    if yml.exists():
        lines = yml.read_text().splitlines()
        start = next(i for i, l in enumerate(lines) if l.strip() == "healthcheck:") + 1
        for k, v in (l.strip().split(":", 1) for l in lines[start:] if l.strip()):
            parts.append(f"health check `{v.strip()}`" if k == "test" else f"{k} {v.strip()}")
    if env_of(c)["CLIENT_MODE"] == "poll":
        parts.append("the test sends `SELECT 1` every 100 ms until it returns")
    return "; ".join(parts)


runs = {}
for c in conds:
    runs[c] = sorted((parse_run(d) for d in (results / c).glob("run*") if (d / "inspect.json").exists()), key=lambda r: r["dir"].name)
    if not runs[c]:
        invalid.append(f"{c}: no runs in results/{c}/")
    for r in runs[c]:
        if r["port0"] is None:
            invalid.append(f"{r['dir'].relative_to(results)}: no `port: 0` line, the data directory was not empty")

ELAPSED = "Elapsed seconds are from the MySQL container's `.State.StartedAt`."


def tests_text(c):
    return f"{len(runs[c])} runs × 2 tests per run: network, published port"


out = ["# mysql-healthy-before-tcp", "", ELAPSED, ""]

r1 = next((r for r in runs.get("baseline", []) if r["dir"].name == "run1"), None)
if r1:
    out += ["## 2. Mechanism", "", f"Job: `{job_of('baseline')}`", ""]
    hcd = r1["dir"] / "healthchecks"
    rows = [l.split("\t") for l in (hcd / "checks.tsv").read_text().splitlines()[1:]] if (hcd / "checks.tsv").exists() else []
    if not rows:
        invalid.append("baseline/run1/healthchecks: no health check comparison")
    out += ["### Health check commands run with `docker exec` in baseline run 1, no credentials", ""]
    out += ["| Command | Exit code during the init script (`sleep`) | Exit code after the real server is listening |", "|---|---|---|"]
    windows = {"during_sleep": (r1["port0"], r1["shutdown"]), "after_real_server": (r1["port3306"], None)}
    groups = {}
    for i in sorted({row[1] for row in rows if row[1] != "tcp"}, key=int):
        cells = {}
        for row in rows:
            if row[1] != i:
                continue
            phase, _, cmd, t, rc = row
            lo, hi = windows[phase]
            if lo is None or float(t) < lo or (hi is not None and float(t) > hi):
                invalid.append(f"baseline/run1/healthchecks: {phase} {cmd} ran at {el(r1, float(t))} s, outside its window")
            err = (hcd / f"{phase}-{i}.stderr").read_text().strip()
            gist = next((l.strip() for l in err.splitlines() if l.startswith("error:")), err.splitlines()[0] if err else "no stderr")
            cells[phase] = (cmd, rc, gist)
        cmd, rc, gist = cells["during_sleep"]
        out.append(f"| `{cmd}` | {rc} | {cells['after_real_server'][1]} |")
        groups.setdefault((rc, gist), []).append(cmd)
    note = "; ".join(f"{code_list(cmds)} exited {rc} printing `{gist}`" for (rc, gist), cmds in groups.items())
    out += ["", f"During `sleep`, {note}.", ""]
    out += ["### Baseline run 1: mysqld log lines", "", "```"]
    out += [r1["lines"].get(k, f"(no {k} line)") for k in ("port0", "port3306")]
    out += ["```", ""]

if runs.get("baseline"):
    rs = runs["baseline"]
    out += ["## 3. Reproduce", ""]
    out += [f"Baseline: job `{job_of('baseline')}`. Init script `sleep 8`; health check {hc_text(rs[0]['hc'])}; empty data directory (`down -v` before each run); the test sends `SELECT 1` once when it sees healthy, from another container on the Compose network and from the host to the published port on `127.0.0.1`.", ""]
    if r1:
        out += ["### Baseline run 1", "", ELAPSED, "", "| Temporary server ready (`port: 0`) | Healthy | Test started `SELECT 1` (network / published port) | Real server ready (`port: 3306`) |", "|---|---|---|---|"]
        conn = " / ".join(el(r1, r1["tests"][v][0][0]) if r1["tests"][v] else "" for v in ("net", "host"))
        out += [f"| {el(r1, r1['port0'])} | {el(r1, r1['healthy'])} | {conn} | {el(r1, r1['port3306'])} |", ""]
    rows = outcomes("baseline")
    out += ["### All baseline runs", ""]
    out += [f"Failed: {sum(t[0][1] != 0 for _, _, t in rows)} of {len(rows)} tests ({tests_text('baseline')}).", ""]
    for v, label in (("net", "network"), ("host", "published port")):
        failed = [t[0][2] for r, vv, t in rows if vv == v and t[0][1] != 0]
        errs = sorted(set(failed))
        if not errs:
            out += [f"No {label} test failed.", ""]
            continue
        head = f"All {len(failed)} failed {label} tests printed:" if len(errs) == 1 else f"The {len(failed)} failed {label} tests printed:"
        out += [head, "", "```"] + errs + ["```", ""]

if r1:
    line = r1["healthy_line"]
    if line and " (" in line:
        line = line.split(" (", 1)[0] + " (... labels omitted)"
    out += ["## 4. Check on your own CI", "", f"Job: `{job_of('baseline')}`", "", "Baseline run 1, `docker events --filter event=health_status`:", "", "```", line or "(no healthy event)", "```", ""]

fixes = [(c, label) for c, label in (("tcp-healthcheck", "Health check over TCP"), ("wait-for-query", "Wait until a query returns")) if runs.get(c)]
if fixes:
    js = {}
    for c, label in fixes:
        js.setdefault(job_of(c), []).append(c)
    out += ["## 5. Fixes", "", "Jobs: " + ", ".join(f"`{j}`" for j in js) + ".", "", ELAPSED, ""]
    out += ["| Fix | Changed from the baseline | Healthy (run 1) | Real server ready (`port: 3306`, run 1) | Result |", "|---|---|---|---|---|"]
    for c, label in fixes:
        r = runs[c][0]
        rows = outcomes(c)
        if env_of(c)["CLIENT_MODE"] == "poll":
            d = [t[-1][3] - rr["port3306"] for rr, _, t in rows if t[-1][1] == 0 and rr["port3306"] is not None]
            res = f"return of the first successful `SELECT 1` − `port: 3306`: {min(d):+.2f} s to {max(d):+.2f} s ({len(d)} of {len(rows)} tests returned; {tests_text(c)})" if d else f"none of {len(rows)} tests returned ({tests_text(c)})"
        else:
            res = f"failed: {sum(t[0][1] != 0 for _, _, t in rows)} of {len(rows)} tests ({tests_text(c)})"
        out.append(f"| {label} | {changed(c)} | {el(r, r['healthy'])} | {el(r, r['port3306'])} | {res} |")
    out.append("")

out += ["## Numbers not used", ""]
if runs.get("wait-for-query"):
    fails = [sum(x[1] != 0 for x in t) for _, _, t in outcomes("wait-for-query")]
    out.append(f"- Wait until a query returns: failed attempts before returning: min {min(fails)}, max {max(fails)}")
out.append("- Times per run and per test: `results/<experiment>/run<N>/` (`mysql.log`, `events.jsonl`, `health_status.txt`, `net.tsv`, `host.tsv`, `inspect.json`; the health check comparison in `results/baseline/run1/healthchecks/`)")
if r1:
    hcd = r1["dir"] / "healthchecks"
    if (hcd / "checks.tsv").exists():
        for phase, _, cmd, t, rc in (l.split("\t") for l in (hcd / "checks.tsv").read_text().splitlines()[1:] if l.split("\t")[1] == "tcp"):
            got = (hcd / f"{phase}-tcp.stdout").read_text().strip()
            out.append(f"- TCP connect from the host to the published port ({phase}): exit code {rc}, bytes read before EOF or 3 s: {got or '(none)'}")
    proxy = [l for l in (r1["dir"] / "docker-proxy.txt").read_text().splitlines() if l.strip()]
    out.append("- docker-proxy for the published port in baseline run 1: " + ("; ".join(f"`{l}`" for l in proxy) or "(none)"))
out.append("")
env_txt = (results / "environment.txt").read_text().splitlines() if (results / "environment.txt").exists() else []
out += ["Environment and userland-proxy:", "", "```"] + env_txt + ["```", ""]
if (results / "timing.tsv").exists():
    out += ["Duration (s):", "", "```"] + (results / "timing.tsv").read_text().splitlines() + ["```"]
if invalid:
    out += ["", "## RUN INVALID", ""] + [f"- {x}" for x in invalid]
print("\n".join(out))
sys.exit(1 if invalid else 0)
