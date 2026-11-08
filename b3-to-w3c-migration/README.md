# b3-to-w3c-migration

Three small Go HTTP servers (a calls b, b calls c, c runs one PostgreSQL query) sit behind Envoy and send spans to Jaeger. The servers are instrumented with `otelhttp` and `otelsql` and pick their propagation format from `OTEL_PROPAGATORS` through `autoprop`; they do not forward trace headers themselves. Each condition sets the Envoy tracer and the three values of `OTEL_PROPAGATORS` for one stage of a move from B3 Multiple Headers to W3C Trace Context, sends `curl` requests without trace headers through Envoy, and reads the spans back from Jaeger and the query comment from the PostgreSQL log.

## Run

`.github/workflows/b3-to-w3c-migration.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. The job `section-3` runs `./run-all.sh 3` and the job `section-5` runs `./run-all.sh 5`. A final job merges their results.

`results/summary.md` goes to the job summary. It has the trace headers c received from b at one stage, and, per condition, the number of traces for one request, the services in each trace, the root spans by service, the services that left spans per request under ratio sampling, whether a service recorded a span under a fixed `traceparent` it was sent, and the comment on the query in the PostgreSQL log. `results/` is uploaded as the `b3-to-w3c-migration-<section>` artifact. The job fails when a request does not return 200, a service does not leave the number of server spans its condition requires, c leaves no database span, or a span falls outside every request.

## Conditions

- `conditions.tsv` has one row per condition: the Envoy configuration in `envoy/`, `OTEL_PROPAGATORS` for a, b and c, the number of requests, the sampling ratio, and which fixed `traceparent` requests are sent. `manual` in the b column runs b without the SDK, forwarding the `x-b3-*` headers it received. The row in the section `control` runs only when named
- `docker-compose.yml` holds what every condition shares. Each condition is its own Compose project
- `results/<condition>/traces/` holds the raw `/api/v3/traces` responses, one per service, and `results/<condition>/requests.tsv` holds the start and end time of each request. Under ratio sampling the spans of one request share no trace ID, so `scripts/analyze.py` assigns a span to a request by its start time; the requests run one at a time
- `results/<condition>/analysis.json` holds everything computed from them, including the database span counts and the trace IDs in the query comment

## Pinned versions

The images in `scripts/versions.env` and `app/Dockerfile`, and the Go modules in `app/go.mod`.
