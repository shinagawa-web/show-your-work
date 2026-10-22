import bisect
import collections
import difflib
import json
import os
import re
import sys

root = sys.argv[1]
conds = sys.argv[2:]
settings_dir = os.path.join(root, "settings")


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def env_file(path):
    out = {}
    for line in read(path).splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip("'\"")
    return out


def origin(c):
    return env_file(os.path.join("conditions", c + ".env")).get("FROM", "")


def effective(c):
    d = os.path.join(settings_dir, c)
    return (read(os.path.join(d, "nginx-T.txt")).splitlines() + read(os.path.join(d, "app.env")).splitlines()
            + read(os.path.join(d, "k6.env")).splitlines())


def access_counts(path):
    counts = collections.Counter()
    pat = re.compile(r'^\S+ "(/items/\d+)" "([^"]*)" (\d+) (\S+)')
    for line in read(path).splitlines():
        m = pat.match(line)
        if not m:
            continue
        who = "cookie" if m.group(2) not in ("", "-") else "no-cookie"
        counts[(who, m.group(4))] += 1
    return counts


def k6_requests(path):
    rows = []
    for line in read(path).splitlines():
        p = line.split()
        if len(p) == 7 and p[0] == "R":
            rows.append((int(p[1]), int(p[2]), int(p[3]), p[4], p[5], p[6]))
    return rows


def latest_logged_in_match(rows):
    by_page = collections.defaultdict(list)
    for t0, t1, page, sender, got, st in rows:
        if sender != "anon":
            by_page[page].append((t1, sender))
    for v in by_page.values():
        v.sort()
    ends = {p: [t for t, _ in v] for p, v in by_page.items()}
    total = match = 0
    for t0, t1, page, sender, got, st in rows:
        if sender == "anon" and got not in ("anon", "none"):
            total += 1
            i = bisect.bisect_right(ends.get(page, []), t0) - 1
            if i >= 0 and by_page[page][i][1] == got:
                match += 1
    return total, match


def curl_markers(text):
    return re.findall(r"<!-- for:(\S+) page:\d+ -->", text)


rows_out = []
checks = []
details = []
for c in conds:
    d = os.path.join(root, c)
    meta = env_file(os.path.join(d, "meta.env"))
    try:
        s = json.loads(read(os.path.join(d, "k6-summary.json")))
    except ValueError:
        s = {}
    total = s.get("req_total", 0)
    li = s.get("req_logged_in", 0)
    other = s.get("other_user_screen", 0)
    lo = s.get("logged_in_got_logged_out_screen", 0)
    k6set = env_file(os.path.join(settings_dir, c, "k6.env"))
    appset = read(os.path.join(settings_dir, c, "app.env")).splitlines()
    rate = int(k6set["RATE"])
    dur = int(k6set["DURATION"].rstrip("s"))
    status = read(os.path.join(d, "run-status.txt")).strip()
    try:
        app_items = int(meta.get("APP_ITEMS_AFTER", "0")) - int(meta.get("APP_ITEMS_BEFORE", "0"))
    except ValueError:
        app_items = -1
    cache_files = read(os.path.join(d, "cache-files.txt")).strip()
    cm = curl_markers(read(os.path.join(d, "curl-check.txt")))
    first = cm[0] if len(cm) > 0 else "none"
    second = cm[1] if len(cm) > 1 else "none"
    fr = origin(c)
    if fr:
        diff = [l for l in difflib.unified_diff(effective(fr), effective(c), lineterm="", n=0)
                if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    else:
        diff = []
    live = read(os.path.join(d, "nginx-T.txt"))
    static = read(os.path.join(settings_dir, c, "nginx-T.txt"))
    acc = access_counts(os.path.join(d, "access.log"))
    rows = k6_requests(os.path.join(d, "k6-requests.log"))
    lm_total, lm_match = latest_logged_in_match(rows)
    pct = lambda a, b: f"{100 * a / b:.1f}" if b else "n/a"
    rows_out.append([c, fr or "-", total, pct(other, total), pct(lo, li), f"{app_items / dur:.1f}",
                     cache_files, s.get("dropped_iterations", "n/a"), first, second])

    checks.append((c, f"run finished (run-status.txt: {status or 'missing'})", status == "ok"))
    checks.append((c, "k6 summary present", bool(s)))
    checks.append((c, "app env in the container == app.env of settings",
                   read(os.path.join(d, "app-env.txt")).splitlines() == appset))
    checks.append((c, "config k6 ran with == k6.env of settings", s.get("config") == k6set))
    checks.append((c, "dropped_iterations == 0", s.get("dropped_iterations") == 0))
    checks.append((c, "invalid_response == 0", s.get("invalid_response") == 0))
    checks.append((c, "req_total within 1 of rate x duration", abs(total - rate * dur) <= 1))
    checks.append((c, "k6 request log lines == req_total", len(rows) == total))
    checks.append((c, "live nginx -T == nginx -T of settings", bool(live) and live == static))
    if fr:
        checks.append((c, f"one changed line against {fr} (nginx -T, app and k6 settings)",
                       sum(1 for l in diff if l.startswith("+")) == 1 and sum(1 for l in diff if l.startswith("-")) <= 1))
    checks.append((c, "curl: first response is the checker's own page", first == "checker"))

    details.append(f"### {c}\n")
    details.append(f"changed from {fr or '-'}:\n```")
    details.extend(diff or ["(none)"])
    details.append("```\n")
    details.append("access.log /items/N (no query) by Cookie and $upstream_cache_status:\n```")
    for k in sorted(acc):
        details.append(f"{k[0]:9} {k[1]:8} {acc[k]}")
    details.append("```\n")
    details.append(f"cookie-less requests that got a user's page: {lm_total}; "
                   f"of them, the user is the latest logged-in requester of that page that finished before the request started: {lm_match}\n")

hdr = ["condition", "from", "requests", "other user's page % of all", "logged-in got logged-out page %",
       "app /items per s", "cache files at end", "dropped_iterations", "curl 1st (cookie)", "curl 2nd (no cookie)"]
out = ["## Results\n", "| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows_out]
out.append("\n## Checks\n")
out += [f"- {c}: {name}: {'pass' if ok else 'FAIL'}" for c, name, ok in checks]
out.append("\n## Details\n")
out += details
text = "\n".join(out) + "\n"
with open(os.path.join(root, "summary.md"), "w") as f:
    f.write(text)
print(text)
sys.exit(0 if all(ok for _, _, ok in checks) else 1)
