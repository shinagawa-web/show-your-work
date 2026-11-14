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


def median(values):
    v = sorted(float(x) for x in values)
    return f"{v[len(v) // 2]:.2f}" if len(v) % 2 else f"{(v[len(v) // 2 - 1] + v[len(v) // 2]) / 2:.2f}"


invalid = []
for c in conds:
    rs = runs(c)
    if not rs:
        invalid.append(f"{c}: no runs")
    for r in rs:
        for line in read(r / "invalid.txt").splitlines():
            invalid.append(f"{c}/{r.name}: {line}")
    if len({outcome(r) for r in rs}) > 1:
        invalid.append(f"{c}: the request was returned in some runs and cut in others")


def rows(c, with_chain):
    rs = runs(c)
    label = table[c]["label"]
    chains = "<br>".join(dict.fromkeys(chain(r) for r in rs))
    lead = f"| {label} | {chains} |" if with_chain else f"| {label} |"
    if len({outcome(r) for r in rs}) == 1:
        return [f"{lead} {median([read(r / 'stop-seconds.txt') for r in rs])} | {outcome(rs[0])} |"]
    return [f"| {label}, run {r.name[3:]} |" + (f" {chain(r)} |" if with_chain else "") + f" {read(r / 'stop-seconds.txt')} | {outcome(r)} |" for r in rs]


judged = "A request is returned when curl received the HTTP 200 response, and cut otherwise."
out = []
p = out.append

opening = [c for c in ("no-handler", "no-handler-init") if c in conds]
if opening:
    p("## Opening: `no-handler`, `no-handler-init`")
    p("")
    p(f"An Express server with no SIGTERM handler, without and with `--init`, stopped with `docker stop` while one `GET /slow?seconds=2` request was in progress. Stop is the median of three runs of the time `docker stop` took. {judged}")
    p("")
    p("| Condition | Stop (s) | Request |")
    p("|---|--:|---|")
    for c in opening:
        out.extend(rows(c, False))
    msgs = list(dict.fromkeys(read(r / "curl-stderr.txt") for c in opening for r in runs(c) if outcome(r) == "cut"))
    if msgs:
        p("")
        p("When the request was cut, curl printed " + ", ".join(f"`{m}`" for m in msgs) + ".")
    p("")

p("## Measurements: all conditions")
p("")
p(f"Each run sends one `GET /slow?seconds=2` request, waits until the server logs that it accepted it, then runs `docker stop`. PID 1 and its children are the `docker top` commands before the request; `docker-entrypoint.sh ...` on the `docker-init` line is the argument it was started with, and the entrypoint has already replaced itself with the next command by `exec`. Stop is the median of three runs of the time `docker stop` took. {judged}")
p("")
p("| Condition | PID 1 and its children | Stop (s) | Request |")
p("|---|---|--:|---|")
for c in conds:
    out.extend(rows(c, True))
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

if invalid:
    p("")
    for line in invalid:
        p(f"RUN INVALID: {line}")

print("\n".join(out))
sys.exit(1 if invalid else 0)
