import json
import re
import sys

HEAD = re.compile(r"^[0-9a-f]+-[0-9a-f]+ ")


def regions(pid):
    cur = None
    with open(f"/proc/{pid}/smaps") as f:
        for line in f:
            if HEAD.match(line):
                if cur is not None:
                    yield cur
                parts = line.split(None, 5)
                cur = {"perms": parts[1], "path": parts[5].strip() if len(parts) > 5 else "", "kb": {}}
            elif cur is not None and line.rstrip().endswith(" kB"):
                k, v = line.split(":", 1)
                cur["kb"][k] = int(v.split()[0])
    if cur is not None:
        yield cur


def main():
    pid = sys.argv[1]
    total_ref = total_anon_in_file = exec_rss = file_rss = 0
    per = {}
    for r in regions(pid):
        if not r["path"].startswith("/"):
            continue
        ref = r["kb"].get("Referenced", 0)
        anon = r["kb"].get("Anonymous", 0)
        rss = r["kb"].get("Rss", 0)
        total_ref += ref
        total_anon_in_file += anon
        file_rss += rss
        if "x" in r["perms"]:
            exec_rss += rss
        p = per.setdefault(r["path"], {"referenced_kb": 0, "anonymous_kb": 0, "rss_kb": 0, "exec_rss_kb": 0})
        p["referenced_kb"] += ref
        p["anonymous_kb"] += anon
        p["rss_kb"] += rss
        if "x" in r["perms"]:
            p["exec_rss_kb"] += rss
    top = sorted(per.items(), key=lambda kv: kv[1]["referenced_kb"] - kv[1]["anonymous_kb"], reverse=True)
    print(json.dumps({
        "touched_kb": total_ref - total_anon_in_file,
        "referenced_kb": total_ref,
        "anonymous_in_file_kb": total_anon_in_file,
        "file_mapped_rss_kb": file_rss,
        "exec_rss_kb": exec_rss,
        "n_files": len(per),
        "top": [dict(path=k, **v) for k, v in top[:25]],
    }, indent=1))


main()
