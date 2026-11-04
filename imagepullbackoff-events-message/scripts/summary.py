import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conditions
import obs

results = sys.argv[1]
parts = sys.argv[2:]
out = []
invalid = []


def p(s=""):
    out.append(s)


def block(text):
    p("```")
    p(text.rstrip("\n"))
    p("```")


def read(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def commands(path):
    text = read(path) or ""
    blocks = {}
    for chunk in re.split(r"^(?=\$ )", text, flags=re.M):
        if chunk.startswith("$ "):
            cmd, _, rest = chunk.partition("\n")
            blocks[cmd[2:]] = re.sub(r"\[exit \d+\]\s*$", "", rest.strip()).rstrip()
    return blocks


def get_pod(dir_, ns, pod):
    return commands(os.path.join(dir_, "get-pods.txt")).get(f"kubectl -n {ns} get pod {pod}", "(not recorded)")


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def check_part(name):
    d = os.path.join(results, name)
    if not os.path.isdir(d):
        invalid.append(f"{name}: no results")
        return None
    for f in ("invalid.txt", "incomplete.txt"):
        t = read(os.path.join(d, f))
        if t:
            invalid.append(f"{name}: {t.strip()}")
    return d


dirs = {n: check_part(n) for n in parts}

causes = dirs.get("causes")
if causes:
    evs = obs.events(os.path.join(causes, "events.jsonl"), "cond")
    pods = os.path.join(causes, "pods.jsonl")
    for c in conditions.CAUSES:
        if c["expect"] == "run" and not obs.running(obs.pod_states(pods, c["name"])):
            invalid.append(f"causes: {c['name']} did not reach Running")
    qps = (read(os.path.join(causes, "events.jsonl")) or "").count("pull QPS exceeded")
    if qps:
        invalid.append(f"causes: `pull QPS exceeded` {qps} times in events.jsonl")

p("# imagepullbackoff-events-message")
p()
for i in invalid:
    p(f"RUN INVALID: {i}")
if invalid:
    p()

NO_CREDS_LINE = ["no-creds-repo-exists", "no-creds-repo-missing", "secret-wrong-host", "secret-other-namespace", "secret-name-typo"]
CONTROL_PAIR = ["hub-missing-secret-name", "hub-missing-secret-name-pull-fails"]
FULL_POD = "tag-missing"


def strip_prefix(message, image):
    q = re.escape(f'"{image}"')
    patterns = [
        rf"Failed to pull image {q}: ",
        r"rpc error: code = \S+ desc = ",
        rf"failed to pull and unpack image {q}: ",
        rf"failed to resolve reference {q}: ",
    ]
    rest = message
    changed = True
    while changed:
        changed = False
        for pat in patterns:
            m = re.match(pat, rest)
            if m:
                rest = rest[m.end():]
                changed = True
    return rest


def without_image(message, image):
    return message.replace(image, "<image>")


def status_of(text):
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return "(not recorded)"
    head = lines[0].split()
    row = lines[1].split()
    return row[head.index("STATUS")] if "STATUS" in head and len(row) > head.index("STATUS") else "(not recorded)"


def image_of(name):
    return next(c["image"] for c in conditions.CAUSES if c["name"] == name)


if causes:
    arch = commands(os.path.join(causes, "environment.txt")).get("docker exec ipb-worker uname -m", "(not recorded)")
    other = re.search(r"^other_arch=(\S+)", read(os.path.join(causes, "images.txt")) or "", re.M)
    p("## 2. Messages per cause (job: causes)")
    p()
    p("Every Pod is created with `imagePullPolicy: Always`. Local registry: `registry:3` with htpasswd, `localhost:5001` mapped to `kind-registry:5000` by `hosts.toml`. "
      f"Node `uname -m`: `{arch}`. `platform-only:1.0` holds only `linux/{other.group(1) if other else '?'}`.")
    p()
    p('The first column is the first `Failed to pull image` event message with these prefixes removed by `scripts/summary.py`: `Failed to pull image "<image>": `, `rpc error: code = <X> desc = `, `failed to pull and unpack image "<image>": `, `failed to resolve reference "<image>": `. In what is left, the image reference of the Pod is replaced by `<image>`.')
    p()
    rows = {}
    for c in conditions.CAUSES:
        if c["expect"] != "fail":
            continue
        f = obs.first_pull_failure(evs, c["name"])
        rest = without_image(strip_prefix(f["message"], c["image"]), c["image"]) if f else None
        rows.setdefault(rest, []).append(c)
    p("Conditions whose rest of the message is the same are on one row. The last column names the conditions of the row that got a `FailedToRetrieveImagePullSecret` event.")
    p()
    p("| Rest of the message | Conditions | `FailedToRetrieveImagePullSecret` |")
    p("|---|---|---|")
    for rest, cs in rows.items():
        conds = "<br>".join(f"{c['what']} (`{c['name']}`)" for c in cs)
        se = ", ".join(f"`{c['name']}`" for c in cs if obs.secret_events(evs, c["name"])) or "none"
        p(f"| {'`' + rest + '`' if rest is not None else '(no event)'} | {conds} | {se} |")
    p()
    p("### Full message")
    p()
    f = obs.first_pull_failure(evs, FULL_POD)
    p(f"First `Failed to pull image` event message of `{FULL_POD}`:")
    p()
    block(f["message"] if f else "(none)")
    p()
    msgs = {}
    for n in NO_CREDS_LINE:
        f = obs.first_pull_failure(evs, n)
        msgs[n] = without_image(f["message"], image_of(n)) if f else None
    same = all(m is not None for m in msgs.values()) and len(set(msgs.values())) == 1
    p(f"First `Failed to pull image` event messages of {', '.join('`' + n + '`' for n in NO_CREDS_LINE)} with the image name replaced by `<image>`: "
      + ("all the same." if same else "not all the same."))
    p()

    p("## 3. Using the table on your own cluster")
    p()
    bp = conditions.BACKOFF_POD
    cmds = commands(os.path.join(causes, f"backoff-{bp}.txt"))
    p(f"### Original error kept during back-off (job: causes, Pod `{bp}`)")
    p()
    jp = next((v for k, v in cmds.items() if "state.waiting.message" in k), "(not recorded)")
    p("`kubectl get pod -o jsonpath='{.status.containerStatuses[*].state.waiting.message}'`, while the Pod was in `ImagePullBackOff`:")
    p()
    block(jp)
    p()
    lines = cmds.get(f"kubectl -n cond describe pod {bp}", "").splitlines()
    excerpt = []
    for i, l in enumerate(lines):
        if l.strip().startswith("State:"):
            excerpt = lines[i:i + 2]
            break
    p("`kubectl describe pod` right after it (excerpt: the `State:` line of the container and the line after it):")
    p()
    block("\n".join(excerpt) or "(not recorded)")
    p()
    p("### imagePullSecrets names a secret that does not exist: two Pods that differ only in the image (job: causes)")
    p()
    p("Both Pods have `imagePullSecrets: no-such-secret`.")
    p()
    p("| Pod | Image | STATUS | `FailedToRetrieveImagePullSecret` event |")
    p("|---|---|---|---|")
    for n in CONTROL_PAIR:
        se = "present" if obs.secret_events(evs, n) else "none"
        p(f"| `{n}` | `{image_of(n)}` | `{status_of(get_pod(causes, 'cond', n))}` | {se} |")
    p()

p("## Numbers not used")
p()
if causes:
    ip = conditions.INTERVAL_POD
    f = obs.pull_failures(evs, ip)
    p(f"### Retries of `{ip}` (job: causes)")
    p()
    p("Each update of the `Failed to pull image` event as received by `kubectl get events --watch`: count and lastTimestamp, and the seconds since the previous lastTimestamp.")
    p()
    rows = []
    prev = None
    for e in f:
        d_ = "" if prev is None else str(int((ts(e["last"]) - prev).total_seconds()))
        rows.append(f"count={e['count']} lastTimestamp={e['last']} {('+' + d_ + 's') if d_ else ''}".rstrip())
        prev = ts(e["last"])
    block("\n".join(rows) or "(none)")
    p()
    seen = obs.statuses_seen(obs.pod_states(pods, ip))
    p(f"STATUS values of `{ip}` in the order they were seen (polled every second):")
    p()
    block(" -> ".join(seen) or "(none)")
    p()
    p("### Docker Hub token requests with the credentials of the three secrets (job: causes)")
    p()
    p("The first line of each block is the user name and the status code of `https://hub.docker.com/v2/users/<name>/`, requested from the machine that runs `run-all.sh`. Then the status code and body of the POST and of the GET to `https://auth.docker.io/token`, sent from `ipb-worker`. The scope is `repository:library/busybox:pull` for the first two blocks and `repository:shingawaweb/imagepullbackoff-private:pull` for the third.")
    p()
    exists = dict(l.split("\t") for l in (read(os.path.join(causes, "hub-user-exists.tsv")) or "").splitlines() if "\t" in l)
    hub = commands(os.path.join(causes, "hub-wrong-creds.txt"))
    for user, code in exists.items():
        lines = [f"{user}: hub.docker.com/v2/users status {code}"]
        for method in ("POST", "GET"):
            for cmd, v in hub.items():
                is_post = "-X POST" in cmd
                if "curl" not in cmd or is_post != (method == "POST"):
                    continue
                if (f"username={user}&" if is_post else f" -u {user}:") not in cmd:
                    continue
                rows = [l for l in v.splitlines() if l.strip()]
                lines.append(f"{method} {rows[-1] if rows else '(no status)'}")
                lines.append("\n".join(rows[:-1]) or "(empty body)")
        block("\n".join(lines))
        p()
    p("### Baselines (job: causes)")
    p()
    for n in ("local-baseline", "hub-baseline"):
        block(get_pod(causes, "cond", n))
        p()
for name, d in dirs.items():
    if not d:
        continue
    env = commands(os.path.join(d, "environment.txt"))
    p(f"### Proxy variables of containerd on each node (job: {name})")
    p()
    lines = []
    for k, v in env.items():
        m = re.match(r"docker exec (\S+) sh -c 'tr .* grep -i proxy'", k)
        if m:
            lines.append(f"{m.group(1)} containerd environ: {' '.join(v.split()) or '(no proxy variables)'}")
    block("\n".join(sorted(lines)) or "(not recorded)")
    p()
print("\n".join(out))
