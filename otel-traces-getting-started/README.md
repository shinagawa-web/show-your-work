# otel-traces-getting-started

This sets up tracing in a kind cluster from an empty environment, step by step: HotROD sends traces to Jaeger directly, then through one OpenTelemetry Collector, then through two. Each step records what the Jaeger API and the Collector show. With two Collector replicas, it also sends spans over one OTLP gRPC connection with `telemetrygen` and records the spans each Collector Pod receives.

## Run

`.github/workflows/otel-traces-getting-started.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

The `s2-s4` job (named after the article sections that use its output) runs `./run-all.sh`, writes `results/summary.md` to the job summary and to a comment on the open pull request of the branch (the same comment is overwritten on every run), and uploads `results/` as the `otel-traces-getting-started-results` artifact.

The summary has a table of the `Traces` lines in each Collector Pod log in section 4, and a table of the spans received by each Collector Pod over one gRPC connection.

## Steps

`run-all.sh` runs these in order on one kind cluster.

| Step | What it does |
|---|---|
| Section 2 | Installs the Jaeger chart, applies `k8s/hotrod.yaml` with HotROD sending to Jaeger, sends three requests to `/dispatch?customer=123`, and records the latest `frontend` trace |
| Section 3 | Installs the Collector chart with `helm/collector-values.yaml`, records the Collector config the chart renders, points HotROD at the Collector with `kubectl set env`, and sends three requests |
| Section 4 | Upgrades the Collector chart to `replicaCount=2`, sends three requests, and counts the `Traces` lines each Collector Pod logged after the requests started |
| One connection | Runs `telemetrygen` as a Pod that sends 100 spans per second over one OTLP gRPC connection for 45 seconds, waits 10 seconds, and takes the increase in `otelcol_receiver_accepted_spans` on each Collector Pod |

HotROD has no probes, so that probe requests do not appear in Jaeger. The Collector exports to Jaeger and to the `debug` exporter, so that its log shows the spans that passed through it.

## Outputs

Under `results/`:

| File | What it records |
|---|---|
| `section2-services.json` | The services the Jaeger API lists |
| `section2-trace.json` | The trace ID, span count and services of the latest `frontend` trace since the requests of section 2 started |
| `section3-config.yaml` | The Collector config the chart renders |
| `section3-collector.log` | `kubectl logs` of the Collector |
| `section3-trace.json` | The trace ID, span count and services of the latest `frontend` trace since the requests of section 3 started |
| `section4-pods.txt` | `kubectl get pods` of the Collector |
| `section4-trace.json` | The trace ID, span count and services of the latest `frontend` trace since the requests of section 4 started |
| `section4-collector-log-lines.txt` | The `Traces` lines each Collector Pod logged after the requests of section 4 started |
| `summary.md` | The two tables of the summary |

## Pinned versions

kind, kubectl, Helm, both charts and `telemetrygen` are at the top of `run-all.sh`. The HotROD image is in `k8s/hotrod.yaml`.
