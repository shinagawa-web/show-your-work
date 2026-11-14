import csv
import pathlib
import sys

results = pathlib.Path(sys.argv[1])
conds = sys.argv[2:]
here = pathlib.Path(__file__).resolve().parent.parent
with open(here / "conditions.tsv") as f:
    table = {row["condition"]: row for row in csv.DictReader(f, delimiter="\t")}


def read(path, default=""):
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return default


def top_rows(run):
    lines = read(run / "docker-top.txt").splitlines()
    if not lines:
        return []
    header = lines[0]
    cmd_at = header.index("CMD")
    rows = []
    for line in lines[1:]:
        f = line.split()
        rows.append({"pid": f[1], "ppid": f[2], "cmd": line[cmd_at:].strip()})
    return rows


def chain(run):
    rows = top_rows(run)
    pids = {r["pid"] for r in rows}
    order = [r for r in rows if r["ppid"] not in pids]
    while True:
        kids = [r for r in rows if order and r["ppid"] == order[-1]["pid"]]
        if not kids:
            break
        order.append(kids[0])
    return " → ".join(f"`{r['cmd']}`" for r in order)


def runs(c):
    return sorted((results / c).glob("run*"), key=lambda p: int(p.name[3:]))


def curl(run):
    rc = read(run / "curl-exit.txt", "?")
    msg = read(run / "curl-stderr.txt") or "-"
    stdout = read(run / "curl-stdout.txt")
    code = stdout.split()[0].split("=")[1] if stdout.startswith("http_code=") else "?"
    return rc, msg, code


def outcome(run):
    rc, _, code = curl(run)
    return "returned" if rc == "0" and code == "200" else "cut"


def joined(values):
    distinct = list(dict.fromkeys(values))
    return distinct[0] if len(distinct) == 1 else ", ".join(values)


invalid = []
for c in conds:
    rs = runs(c)
    if not rs:
        invalid.append(f"{c}: no runs")
    for r in rs:
        for line in read(r / "invalid.txt").splitlines():
            invalid.append(f"{c}/{r.name}: {line}")

out = []
p = out.append

opening = [c for c in ("no-handler", "no-handler-init") if c in conds]
if opening:
    p("## Opening: `no-handler`, `no-handler-init`")
    p("")
    p("`docker stop` while one `GET /slow?seconds=2` request was in progress, on an Express server with no SIGTERM handler, without and with `--init`. Stop is the time `docker stop` took; the curl columns are the result of the request in progress.")
    p("")
    p("| Condition | Run | Stop (s) | Exit code | curl exit code | curl message | HTTP status |")
    p("|---|--:|--:|--:|--:|---|--:|")
    for c in opening:
        for r in runs(c):
            rc, msg, code = curl(r)
            p(f"| {table[c]['label']} | {r.name[3:]} | {read(r / 'stop-seconds.txt')} | {read(r / 'exit-code.txt')} | {rc} | {msg} | {code} |")
    p("")

p("## Measurements: all conditions")
p("")
p("Each run sends one `GET /slow` request (2 s), waits until the server logs that it accepted it, then runs `docker stop`. PID 1 and its children are from `docker top` before the request. Stop is the time `docker stop` took in each run; Request is whether curl got the HTTP 200 response.")
p("")
n = max((len(runs(c)) for c in conds), default=0)
p("| Condition | PID 1 and its children | " + " | ".join(f"Stop, run {i + 1} (s)" for i in range(n)) + " | Exit code | Request |")
p("|---|---|" + "--:|" * n + "--:|---|")
for c in conds:
    rs = runs(c)
    chains = list(dict.fromkeys(chain(r) for r in rs))
    stops = [read(r / "stop-seconds.txt") for r in rs] + [""] * (n - len(rs))
    p(f"| {table[c]['label']} | {'<br>'.join(chains)} | " + " | ".join(stops) + f" | {joined([read(r / 'exit-code.txt') for r in rs])} | {joined([outcome(r) for r in rs])} |")
p("")

check = [c for c in ("handler-init", "shell-form-init") if c in conds]
if check:
    p("## Checking the fix: `handler-init`, `shell-form-init`")
    p("")
    p("Run 1 of each condition: `docker top` before the request, and the container log (`docker logs -t`) up to the end of `docker stop`.")
    for c in check:
        r = runs(c)[0]
        p("")
        p(f"### {table[c]['label']}")
        p("")
        p("```")
        p(f"$ docker top dicr-{c}-1")
        p(read(r / "docker-top.txt"))
        p(f"$ docker logs -t dicr-{c}-1")
        p(read(r / "app.log"))
        p("```")
    p("")

p("## Versions")
p("")
p("```")
env = read(results / "environment.txt").splitlines()
for i, line in enumerate(env):
    if line.startswith("$ ") and (line.endswith("--version") or "Server.Version" in line):
        p(line)
        if i + 1 < len(env):
            p(env[i + 1])
p("```")

if invalid:
    p("")
    for line in invalid:
        p(f"RUN INVALID: {line}")

print("\n".join(out))
sys.exit(1 if invalid else 0)
