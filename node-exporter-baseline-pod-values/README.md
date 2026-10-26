# node-exporter-baseline-pod-values

This installs the prometheus-node-exporter Helm chart on a one-node k3s cluster as six variants of its Pod spec, one namespace each, and records what Pod Security Admission prints for the Baseline level, what each variant reports on `/metrics` next to the same quantities read on the node, and how the conntrack alert expression of kube-prometheus-stack evaluates while the node's conntrack table is filled above the alert's threshold, to the share set in `CONNTRACK_RATIO_PERCENT` in `run-all.sh`. It also installs kube-prometheus-stack with node-exporter moved to its own namespace and records the Baseline dry-run of the Prometheus namespace.

## Run

`.github/workflows/node-exporter-baseline-pod-values.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

`run-all.sh` takes the sections to run as arguments, and runs all of them without arguments. Each section starts from installing k3s:

| Section | What it runs |
|---|---|
| `s1` | The chart default and its Baseline dry-run, with the versions in use |
| `s2` | All six variants next to the node, the Baseline dry-run of each, and the conntrack alert expression |
| `s3` | `kubectl get daemonset -o yaml` of every variant except the one without `hostPID` |
| `s4` | kube-prometheus-stack with node-exporter in its own namespace labelled `enforce=privileged`, and the Baseline dry-run of the Prometheus namespace |

`results/summary.md` goes to the job summary, with one part per section run, and the outputs that only back the others up under its last heading, `Numbers not used`. Every raw output under `results/`, including each scrape under `results/metrics/` and `results/daemonset-<variant>.yaml`, is uploaded as the `node-exporter-baseline-pod-values-results` artifact. A run that did not collect the data (`scripts/setup.sh` failed, a variant or the Prometheus did not roll out, the Prometheus did not see all six targets up, `scripts/holder.py` did not open its connections, the node's conntrack count was not above the alert's threshold when the expression was evaluated, the conntrack table overflowed, `scripts/cells.py` failed, kube-prometheus-stack was not installed or its Prometheus or node-exporter Pod did not exist before the dry-run, an output that the summary reads is missing) fails the job with a `RUN INVALID` line at the top of the summary; a verdict or dry-run that differs from the prediction never fails it.

## Variants

Each variant is one case in `scripts/render.sh`: the chart default, one value changed at a time (`hostNetwork`, `hostPID`, `hostRootFsMount.enabled`), all three together, and all three with the `/proc` and `/sys` hostPath volumes removed and the arguments pointed at the container's own `/proc` and `/sys`. Variants on the host network use separate ports.

`k8s/prometheus.yaml` is a Prometheus that scrapes the six variants with the `endpoints` role under the job name `node-exporter`; `run-all.sh` fills in the image from `scripts/versions.env` when it applies it. `scripts/holder.py` holds TCP connections open on the node, and `run-all.sh` lowers `nf_conntrack_max` so that the held count is the share set in `CONNTRACK_RATIO_PERCENT`, then restores it.

`scripts/cells.py` scrapes each variant directly, reads the node's and the Pod's own values before and after the scrape, and writes one verdict per series group and variant to `results/cells.txt`. `scripts/summary.py` writes `results/summary.md`.

## Pinned versions

`scripts/versions.env`. The node-exporter image is the default of that chart version, recorded in `results/00-environment.txt`.
