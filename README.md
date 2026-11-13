# show-your-work

English | [日本語](README.ja.md)

Each folder is one verification, run by `.github/workflows/<folder>.yml` on GitHub Actions. Fork the repository, enable Actions and run the workflow; the summary is in the job summary and the raw output in the artifacts.

| Verification | Article |
|---|---|
| [b3-to-w3c-migration](b3-to-w3c-migration/README.md) | — |
| [cached-301-stale-redirect](cached-301-stale-redirect/README.md) | [301 redirect change doesn't take effect for some users](https://dev.to/shinagawa-web/301-redirect-change-doesnt-take-effect-for-some-users-3gh4) |
| [cgroup-v2-scripts-read-wrong-values](cgroup-v2-scripts-read-wrong-values/README.md) | [K8s upgrade removes cgroup paths and changes how you read the values](https://dev.to/shinagawa-web/k8s-upgrade-drops-a-path-fixed-scripts-read-other-values-1nje) |
| [chunk-load-heuristic-freshness](chunk-load-heuristic-freshness/README.md) | [Buttons don't work for some users after a deploy](https://dev.to/shinagawa-web/deploy-weekly-and-the-previous-version-keeps-running-for-17-hours-1ji0) |
| [cluster-autoscaler-nodes-not-removed](cluster-autoscaler-nodes-not-removed/README.md) | — |
| [cpu-quota-throttling-p99](cpu-quota-throttling-p99/README.md) | [Only p99 is slow at low CPU usage: the cause is the CPU limit](https://dev.to/shinagawa-web/high-p99-latency-at-low-cpu-usage-is-caused-by-cpu-throttling-gm9) |
| [distroless-ephemeral-debug](distroless-ephemeral-debug/README.md) | [No shell in a distroless Pod: what kubectl debug can and can't see](https://dev.to/shinagawa-web/no-shell-in-a-distroless-pod-what-kubectl-debug-can-and-cant-see-122o) |
| [exit137-sender](exit137-sender/README.md) | [Docker exit code 137: telling the causes apart by the logs](https://dev.to/shinagawa-web/docker-exit-code-137-telling-the-causes-apart-from-the-logs-3kdg) |
| [g1-heap-not-returned](g1-heap-not-returned/README.md) | [After upgrading from Java 8, G1 stopped returning memory](https://dev.to/shinagawa-web/after-upgrading-from-java-8-g1-stopped-returning-memory-38n) |
| [glibc-heap-not-returned](glibc-heap-not-returned/README.md) | — |
| [hpa-reverts-manual-scale](hpa-reverts-manual-scale/README.md) | [HPA does not keep the replicas you set with kubectl scale](https://dev.to/shinagawa-web/hpa-does-not-keep-the-replicas-you-set-with-kubectl-scale-2jpg) |
| [imagepullbackoff-events-message](imagepullbackoff-events-message/README.md) | — |
| [io-max-hidden-from-iostat](io-max-hidden-from-iostat/README.md) | — |
| [istio-outlier-slow-pod](istio-outlier-slow-pod/README.md) | — |
| [liveness-probe-overload-restart](liveness-probe-overload-restart/README.md) | — |
| [local-cache-cold-pods](local-cache-cold-pods/README.md) | — |
| [memory-limit-refault-before-oom](memory-limit-refault-before-oom/README.md) | — |
| [mysql-healthy-before-tcp](mysql-healthy-before-tcp/README.md) | — |
| [mysql-index-unusable](mysql-index-unusable/README.md) | [Telling apart 3 reasons MySQL isn't using your index](https://dev.to/shinagawa-web/telling-apart-3-reasons-mysql-isnt-using-your-index-3hi8) |
| [nginx-accept-queue-overflow](nginx-accept-queue-overflow/README.md) | [Slow 200s in nginx, fast in the application server's logs](https://dev.to/shinagawa-web/nginx-upstreamconnecttime-comes-in-1-second-steps-357a) |
| [nginx-cache-shows-other-users](nginx-cache-shows-other-users/README.md) | — |
| [node-exporter-baseline-pod-values](node-exporter-baseline-pod-values/README.md) | — |
| [nodejs-eventloop-starvation-p99](nodejs-eventloop-starvation-p99/README.md) | [High p99 latency in Node.js at low CPU usage: a blocked event loop](https://dev.to/shinagawa-web/high-p99-latency-in-nodejs-at-low-cpu-usage-a-blocked-event-loop-5b0k) |
| [otel-traces-getting-started](otel-traces-getting-started/README.md) | — |
| [probe-wait-by-server-type](probe-wait-by-server-type/README.md) | — |
| [provisional-hold-inventory-stockout](provisional-hold-inventory-stockout/README.md) | — |
| [rate-limit-cannot-protect](rate-limit-cannot-protect/README.md) | [nginx 504 Gateway Timeout with zero 429s from limit_req](https://dev.to/shinagawa-web/nginx-504-gateway-timeout-with-zero-429s-from-limitreq-31pd) |
| [read-committed-lost-update-patterns](read-committed-lost-update-patterns/README.md) | [Negative stock and lost updates in a Postgres transaction](https://dev.to/shinagawa-web/negative-stock-and-lost-updates-in-a-postgres-transaction-5hb) |
| [select-for-update-throughput-ceiling](select-for-update-throughput-ceiling/README.md) | [Orders stall only during sales: work done while holding a SELECT FOR UPDATE lock](https://dev.to/shinagawa-web/postgres-for-update-caps-throughput-at-1-lock-hold-time-3598) |
| [transient-502-keepalive-reuse](transient-502-keepalive-reuse/README.md) | — |
| [turborepo-cache-misses](turborepo-cache-misses/README.md) | — |
