import json
import pathlib
import subprocess
import sys
import time

out, cluster, fqdn, expect_max, expect_timeout = sys.argv[1:6]
out = pathlib.Path(out)


def run(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def check():
    raw = run("istioctl", "proxy-config", "cluster", "k6.demo", "--fqdn", fqdn, "--port", "8080", "-o", "json")
    clusters = [c for c in json.loads(raw or "[]") if c.get("name") == cluster]
    if not clusters:
        return "cluster not found", None, None
    od = clusters[0].get("outlierDetection")
    if od is None:
        return "no outlierDetection on the cluster", clusters[0], None
    if od.get("consecutive5xx") != 5:
        return f"consecutive5xx is {od.get('consecutive5xx')}", clusters[0], None
    if str(od.get("maxEjectionPercent", "none")) != expect_max:
        return f"maxEjectionPercent is {od.get('maxEjectionPercent')}", clusters[0], None
    raw = run("istioctl", "proxy-config", "route", "k6.demo", "--name", "8080", "-o", "json")
    routes = [
        r for rc in json.loads(raw or "[]") for vh in rc.get("virtualHosts", [])
        if any(d.startswith(fqdn) for d in vh.get("domains", [])) for r in vh.get("routes", [])
    ]
    if not routes:
        return "route not found", clusters[0], None
    timeout = routes[0].get("route", {}).get("timeout", "none")
    want = 0.0 if expect_timeout == "none" else int(expect_timeout.removesuffix("ms")) / 1000
    if timeout == "none" or float(timeout.removesuffix("s")) != want:
        return f"route timeout is {timeout}, want {want}s", clusters[0], routes
    eps = run("istioctl", "proxy-config", "endpoint", "k6.demo", "--cluster", cluster)
    if eps.count("HEALTHY") != 3:
        return f"endpoints: {eps}", clusters[0], routes
    return None, clusters[0], routes


for _ in range(30):
    err, c, r = check()
    if err is None:
        break
    time.sleep(1)
(out / "cluster.json").write_text(json.dumps(c, indent=1) + "\n")
(out / "routes.json").write_text(json.dumps(r, indent=1) + "\n")
if err:
    (out / "preflight.err").write_text(err)
    sys.exit(1)
