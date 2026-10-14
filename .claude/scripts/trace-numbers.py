#!/usr/bin/env python3
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[2]
NUM = re.compile(r"(?<![A-Za-z0-9_.\-/#^])-?(?<![A-Za-z0-9_./#^])\d+(?:\.\d+)?")
CLAIM = re.compile(r"^([<>~])(\S+)$")

problems = []


def problem(msg):
    problems.append(msg)
    print(f"NG  {msg}")


def info(msg):
    print(f"--  {msg}")


def sh(*args, check=True):
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}: {r.stderr.strip()}")
    return r


def gh_json(*args):
    return json.loads(sh("gh", *args).stdout)


def mask(text):
    out = list(text)

    def blank(a, b):
        for i in range(a, b):
            if out[i] != "\n":
                out[i] = " "

    m = re.match(r"---\n.*?\n---\n", text, re.S)
    if m:
        blank(*m.span())
    for m in re.finditer(r"\]\([^)]*\)", text):
        blank(m.start() + 1, m.end())
    for m in re.finditer(r"https?://\S+", text):
        blank(*m.span())
    for m in re.finditer(r"\[\^[^\]]*\]", text):
        blank(*m.span())
    return "".join(out)


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def occurrences(body, needle):
    spans, i = [], body.find(needle)
    while needle and i >= 0:
        spans.append((i, i + len(needle)))
        i = body.find(needle, i + len(needle))
    return spans


def tables(body):
    blocks, start, pos = [], None, 0
    for line in body.splitlines(keepends=True):
        if line.lstrip().startswith("|"):
            if start is None:
                start = pos
        elif start is not None:
            blocks.append((start, pos))
            start = None
        pos += len(line)
    if start is not None:
        blocks.append((start, pos))
    return blocks


class Source:
    def __init__(self, repo, commit, cache):
        self.repo, self.commit, self.cache = repo, commit, cache

    def fetch(self, run, artifact):
        d = self.cache / run / artifact
        if not d.exists():
            r = sh("gh", "run", "download", run, "-R", self.repo, "-n", artifact, "-D", str(d), check=False)
            if r.returncode:
                shutil.rmtree(d, ignore_errors=True)
                raise RuntimeError(f"artifact {artifact} of run {run} cannot be downloaded: {r.stderr.strip()}")
        return d

    def values(self, row, run):
        if run == "code":
            content = sh("git", "show", f"{self.commit}:{row['file']}").stdout
        else:
            path = self.fetch(run, row["artifact"]) / row["file"]
            if not path.exists():
                raise RuntimeError(f"{row['artifact']}/{row['file']} is not in run {run}")
            if path.suffix in (".json", ".jsonl"):
                return sh("jq", "-r", row["selector"], str(path)).stdout.split()
            content = path.read_text(errors="replace")
        rx = re.compile(row["selector"], re.M)
        if not rx.groups:
            return [str(len(rx.findall(content)))]
        m = rx.search(content)
        if not m:
            raise RuntimeError(f"selector matched nothing in {row['file']}")
        return list(m.groups())


def scaled(values, scale):
    try:
        return [Decimal(v) * scale for v in values]
    except InvalidOperation:
        raise RuntimeError(f"not a number: {values}")


def fmt(d):
    return f"{float(d):.4g}"


def compare(expected, got):
    if len(expected) != len(got):
        return f"{len(got)} values for {len(expected)} numbers"
    rounded = [g.quantize(Decimal(1).scaleb(Decimal(e).as_tuple().exponent), ROUND_HALF_UP) for e, g in zip(expected, got)]
    if any(Decimal(e) != r for e, r in zip(expected, rounded)):
        return "source gives " + " / ".join(str(r) for r in rounded)
    return None


class Code:
    def __init__(self, commit, slug):
        self.commit, self.slug = commit, slug
        wf = f".github/workflows/{slug}.yml"
        self.paths = [slug, wf]
        r = sh("git", "show", f"{commit}:{wf}", check=False)
        self.paths += sorted(set(re.findall(r"uses:\s*\./(\S+)", r.stdout)))

    def same(self, sha):
        if sh("git", "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode:
            sh("git", "fetch", "-q", "origin", sha, check=False)
        r = sh("git", "diff", "--quiet", sha, self.commit, "--", *self.paths, check=False)
        return r.returncode == 0


def check_run(repo, code, run):
    v = gh_json("run", "view", run, "-R", repo, "--json", "headSha,conclusion,workflowName,status")
    if v["workflowName"] != code.slug:
        problem(f"run {run} is workflow {v['workflowName']}, not {code.slug}")
        return None
    if v["conclusion"] != "success":
        problem(f"run {run} concluded {v['conclusion'] or v['status']}")
    if not code.same(v["headSha"]):
        problem(f"run {run} ran {v['headSha'][:7]}, whose {', '.join(code.paths)} differ from {code.commit}")
        return None
    return v["headSha"]


def dispatch(repo, code, ref, n):
    sha = gh_json("api", f"repos/{repo}/commits/{ref}")["sha"]
    if not code.same(sha):
        problem(f"--ref {ref} ({sha[:7]}) differs from {code.commit} under {', '.join(code.paths)}; not dispatched")
        return []
    since = datetime.now(timezone.utc) - timedelta(seconds=5)
    for _ in range(n):
        sh("gh", "workflow", "run", f"{code.slug}.yml", "-R", repo, "--ref", ref)
    ids, deadline = [], time.time() + 180
    while len(ids) < n and time.time() < deadline:
        time.sleep(10)
        runs = gh_json("run", "list", "-R", repo, "-w", f"{code.slug}.yml", "-e", "workflow_dispatch", "-b", ref,
                       "-L", "50", "--json", "databaseId,createdAt")
        ids = [str(r["databaseId"]) for r in runs
               if datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")) >= since]
    if len(ids) < n:
        problem(f"found {len(ids)} of {n} dispatched runs")
    pending = set(ids)
    while pending:
        time.sleep(30)
        for run in list(pending):
            if gh_json("run", "view", run, "-R", repo, "--json", "status")["status"] == "completed":
                pending.discard(run)
        print(f"waiting for {len(pending)} of {len(ids)} runs", file=sys.stderr)
    return ids


def spread(rows, src, extra):
    print("\n## Spread across runs\n")
    series, constant = {}, 0
    for row in rows:
        per = {}
        for key, run in [("cited", row["run"])] + [(r, r) for r in extra]:
            try:
                per[key] = scaled(src.values(row, run), row["scale"])
            except RuntimeError as e:
                info(f"{row['id']} in run {run}: {e}")
        series[row["id"]] = per
        cols = list(zip(*per.values()))
        if len({len(v) for v in per.values()}) > 1:
            counts = ", ".join(f"{k} {len(v)}" for k, v in per.items())
            print(f"{row['id']}\t{row['text']}\tn={len(per)}\tnumber of values differs: {counts}")
        elif any(min(c) != max(c) for c in cols):
            stats = "; ".join(f"{fmt(min(c))} / {fmt(median(c))} / {fmt(max(c))}" for c in cols)
            print(f"{row['id']}\t{row['text']}\tn={len(per)}\tmin / median / max {stats}")
        else:
            constant += 1
    info(f"{constant} rows give the same values in every run")
    print()
    for row in rows:
        if not row["vs"]:
            continue
        m = CLAIM.match(row["vs"])
        if not m or m[2] not in series:
            problem(f"{row['id']}: vs '{row['vs']}' must be >id, <id or ~id of a traced row")
            continue
        op, other = m[1], m[2]
        a, b = series[row["id"]], series[other]
        keys = [k for k in a if k in b]
        if any(len(v) != 1 for v in [*a.values(), *b.values()]):
            problem(f"{row['id']} {op} {other}: vs compares rows with a single value only")
            continue
        if len(keys) < 3:
            problem(f"{row['id']} {op} {other}: {len(keys)} paired runs, at least 3 needed")
            continue
        xa, xb = [a[k][0] for k in keys], [b[k][0] for k in keys]
        overlap = not (max(xa) < min(xb) or max(xb) < min(xa))
        label = f"{row['id']} ({row['text']}) {op} {other}: {row['id']} {fmt(min(xa))}..{fmt(max(xa))}, {other} {fmt(min(xb))}..{fmt(max(xb))} over {len(keys)} runs"
        if op == "~":
            (info if overlap else problem)(label + ("" if overlap else ", ranges do not overlap"))
        else:
            held = all((x > y) if op == ">" else (x < y) for x, y in zip(xa, xb))
            if held and not overlap:
                info(label)
            else:
                problem(label + (", sign flips in some runs" if not held else ", ranges overlap"))


def main():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Checks every number in an article against the CI run it comes from.",
        epilog="""numbers file: TSV with the header id, text, run, artifact, file, selector, scale, vs, note

  text      literal text from the article. Numbers in {braces} are checked; without braces every number is.
            Every number in the article must sit inside the text of some row.
  run       a run ID linked from the article, 'code' for a file of the repository at the pinned commit,
            or '-' for a number that is not traced (note says why)
  artifact  artifact name of the run
  file      path inside the artifact, or inside the repository for 'code'
  selector  jq filter for .json and .jsonl, regular expression otherwise: its groups give the values,
            or without groups the number of matches. Gives one value per checked number, in order
  scale     multiplier applied to the values (default 1)
  vs        >id, <id or ~id: the article says this value is above, below or about the same as row id.
            Checked against run-to-run spread with --runs or --repeat (at least 3 runs)

The article must link the code at a commit (github.com/<repo>/tree/<sha>/<slug>) and the runs it uses.""",
    )
    ap.add_argument("article", type=Path)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--commit")
    ap.add_argument("--slug")
    ap.add_argument("--runs", default="")
    ap.add_argument("--repeat", type=int, default=0)
    ap.add_argument("--ref", default="main")
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "trace-numbers")
    a = ap.parse_args()

    repo = sh("gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").stdout.strip()
    raw = a.article.read_text()
    body = mask(raw)

    print("## Article\n")
    pins = set(re.findall(rf"github\.com/{re.escape(repo)}/(?:tree|blob)/([0-9a-f]{{7,40}})/([A-Za-z0-9_.-]+)", raw))
    cited = set(re.findall(rf"github\.com/{re.escape(repo)}/actions/runs/(\d+)", raw))
    commits, slugs = {c for c, _ in pins}, {s for _, s in pins}
    commit = a.commit or (len(commits) == 1 and next(iter(commits))) or None
    slug = a.slug or (len(slugs) == 1 and next(iter(slugs))) or None
    if not commit:
        problem(f"article pins {'no' if not commits else 'more than one'} commit of the code; pass --commit")
    if not slug:
        problem(f"article links {'no' if not slugs else 'more than one'} folder; pass --slug")
    if not commit or not slug:
        return 1
    code = Code(commit, slug)
    info(f"code {slug} at {commit}; runs cited: {', '.join(sorted(cited)) or 'none'}")
    if not code.same("HEAD"):
        info(f"{', '.join(code.paths)} changed between {commit} and HEAD")

    with a.manifest.open(newline="") as f:
        rows = [r for r in csv.DictReader(f, delimiter="\t") if any((v or "").strip() for v in r.values())]
    for r in rows:
        for k in ("id", "text", "run", "artifact", "file", "selector", "scale", "vs", "note"):
            r[k] = (r.get(k) or "").strip()
    ids = [r["id"] for r in rows]
    for d in sorted({i for i in ids if ids.count(i) > 1}):
        problem(f"id {d} is used more than once")

    print("\n## Runs\n")
    runs = sorted({r["run"] for r in rows if r["run"].isdigit()})
    good = set()
    for run in runs:
        if run not in cited:
            problem(f"run {run} is used by the manifest but not linked from the article")
        if check_run(repo, code, run):
            good.add(run)
            info(f"run {run}: same code as {commit}")
    for run in sorted(cited - set(runs)):
        info(f"run {run} is linked from the article but no number comes from it")

    print("\n## Numbers\n")
    src = Source(repo, commit, a.cache)
    covered, traced, declared = [], [], []
    for r in rows:
        r["needle"] = r["text"].replace("{", "").replace("}", "")
        spans = occurrences(body, r["needle"])
        if not spans:
            problem(f"{r['id']}: '{r['needle']}' is not in the article")
            continue
        r["spans"] = spans
        covered += spans
        if r["run"] == "-":
            if not r["note"]:
                problem(f"{r['id']}: '{r['text']}' is declared untraced without a note")
            declared.append(r)
            continue
        marked = re.findall(r"\{([^}]*)\}", r["text"])
        expected = [n for m in marked for n in NUM.findall(m)] if marked else NUM.findall(r["needle"])
        if not expected:
            problem(f"{r['id']}: '{r['text']}' has no number")
            continue
        if r["run"] != "code" and r["run"] not in good:
            problem(f"{r['id']}: '{r['text']}' comes from run {r['run'] or '(empty)'}, which is not usable")
            continue
        try:
            r["scale"] = Decimal(r["scale"] or "1")
            diff = compare(expected, scaled(src.values(r, r["run"]), r["scale"]))
        except (RuntimeError, InvalidOperation) as e:
            problem(f"{r['id']}: '{r['text']}' line {line_of(raw, spans[0][0])}: {e}")
            continue
        if diff:
            problem(f"{r['id']}: '{r['text']}' line {line_of(raw, spans[0][0])}: {diff}")
        else:
            traced.append(r)
    info(f"{len(traced)} of {len(rows) - len(declared)} traced rows match their source")

    print("\n## Numbers in the article with no row\n")
    for m in NUM.finditer(body):
        if not any(s <= m.start() < e for s, e in covered):
            ln = line_of(raw, m.start())
            ctx = body[max(0, m.start() - 15):m.end() + 10].replace("\n", " ").strip()
            problem(f"line {ln}: {m.group(0)} in '{ctx}'")

    print("\n## Tables mixing runs\n")
    for s, e in tables(body):
        used = {r["run"] for r in rows if r["run"].isdigit() and any(s <= p < e for p, _ in r.get("spans", []))}
        if len(used) > 1:
            problem(f"table at line {line_of(raw, s)} takes numbers from runs {', '.join(sorted(used))}")

    print("\n## Declared untraced\n")
    for r in declared:
        print(f"{r['id']}\t{r['text']}\t{r['note']}")

    extra = [x for x in a.runs.split(",") if x]
    if a.repeat:
        extra += dispatch(repo, code, a.ref, a.repeat)
    extra = [x for x in dict.fromkeys(extra) if x not in runs and check_run(repo, code, x)]
    if extra:
        spread([r for r in traced if r["run"] != "code"], src, extra)
    elif any(r["vs"] for r in rows):
        problem("comparisons (vs) are not checked against run-to-run spread; pass --runs or --repeat")

    print(f"\n{len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
