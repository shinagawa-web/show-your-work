# transient-502-keepalive-reuse

Puts nginx, which pools keepalive connections to its backend, in front of two Node.js instances, closes the pooled connections from the backend side under load, and records the 502s nginx returns: nginx and backend access logs, the nginx error log, a loopback packet capture of FIN, RST and PSH packets, and `nstat` counters. Each scenario is one case in `scripts/scenario.sh`.

## Run

`.github/workflows/transient-502-keepalive-reuse.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. Each job runs one scenario.

`results/summary.md` goes to the job summary: the environment, `summary.txt` (requests, 502s, the error log and `nstat`) and the output of `scripts/analyze.py` (502s per turnover, the packet sequence of reset connections, retries, and the difference between requests sent and requests the backend received). `results/` is uploaded as the `transient-502-keepalive-reuse-<scenario>` artifact.

## Pinned versions

The images in `Dockerfile`.
