# show-your-work

[English](README.md) | 日本語

各フォルダが1つの検証で、GitHub Actions の `.github/workflows/<フォルダ名>.yml` で動きます。リポジトリを fork して Actions を有効にし、ワークフローを実行してください。サマリーはジョブサマリーに、生の出力は artifact に出ます。

| 検証 | 記事 |
|---|---|
| [b3-to-w3c-migration](b3-to-w3c-migration/README.md) | — |
| [cached-301-stale-redirect](cached-301-stale-redirect/README.md) | [301 リダイレクトの変更が一部の利用者に反映されない](https://zenn.dev/shinagawa_web/articles/cached-301-stale-redirect) |
| [cgroup-v2-scripts-read-wrong-values](cgroup-v2-scripts-read-wrong-values/README.md) | [K8s 更新で cgroup のパスが消え、値の読み方も変わる](https://zenn.dev/shinagawa_web/articles/cgroup-v2-scripts-read-wrong-values) |
| [chunk-load-heuristic-freshness](chunk-load-heuristic-freshness/README.md) | [デプロイ後に一部ユーザーだけボタンが動かない](https://zenn.dev/shinagawa_web/articles/chunk-load-heuristic-freshness) |
| [cluster-autoscaler-nodes-not-removed](cluster-autoscaler-nodes-not-removed/README.md) | — |
| [cpu-quota-throttling-p99](cpu-quota-throttling-p99/README.md) | [CPU使用率が低いのにp99だけ遅い原因はCPU上限](https://zenn.dev/shinagawa_web/articles/cpu-quota-throttling-p99) |
| [distroless-ephemeral-debug](distroless-ephemeral-debug/README.md) | — |
| [exit137-sender](exit137-sender/README.md) | [Dockerのexit 137の原因をログで見分ける](https://zenn.dev/shinagawa_web/articles/exit137-sender) |
| [g1-heap-not-returned](g1-heap-not-returned/README.md) | [Java 8 から上げたら G1 がメモリを解放しなくなった](https://zenn.dev/shinagawa_web/articles/g1-heap-not-returned) |
| [glibc-heap-not-returned](glibc-heap-not-returned/README.md) | — |
| [hpa-reverts-manual-scale](hpa-reverts-manual-scale/README.md) | [HPA は kubectl scale の台数を守らない](https://zenn.dev/shinagawa_web/articles/hpa-reverts-manual-scale) |
| [iam-policy-scanners](iam-policy-scanners/README.md) | — |
| [imagepullbackoff-events-message](imagepullbackoff-events-message/README.md) | — |
| [io-max-hidden-from-iostat](io-max-hidden-from-iostat/README.md) | — |
| [istio-outlier-slow-pod](istio-outlier-slow-pod/README.md) | — |
| [liveness-probe-overload-restart](liveness-probe-overload-restart/README.md) | — |
| [local-cache-cold-pods](local-cache-cold-pods/README.md) | — |
| [memory-limit-refault-before-oom](memory-limit-refault-before-oom/README.md) | — |
| [mysql-healthy-before-tcp](mysql-healthy-before-tcp/README.md) | — |
| [mysql-index-unusable](mysql-index-unusable/README.md) | [MySQLでインデックスが効かない3つの原因を見分ける](https://zenn.dev/shinagawa_web/articles/mysql-index-unusable) |
| [nginx-accept-queue-overflow](nginx-accept-queue-overflow/README.md) | [nginxでは遅い200が、アプリケーションサーバーのログでは速い](https://zenn.dev/shinagawa_web/articles/nginx-accept-queue-overflow) |
| [nginx-cache-shows-other-users](nginx-cache-shows-other-users/README.md) | [nginxのキャッシュで他人の名前が出る](https://zenn.dev/shinagawa_web/articles/nginx-cache-shows-other-users) |
| [node-exporter-baseline-pod-values](node-exporter-baseline-pod-values/README.md) | — |
| [nodejs-eventloop-starvation-p99](nodejs-eventloop-starvation-p99/README.md) | [Node.jsのp99だけ悪化する原因はイベントループ詰まり](https://zenn.dev/shinagawa_web/articles/nodejs-eventloop-starvation-p99) |
| [otel-traces-getting-started](otel-traces-getting-started/README.md) | — |
| [probe-wait-by-server-type](probe-wait-by-server-type/README.md) | — |
| [provisional-hold-inventory-stockout](provisional-hold-inventory-stockout/README.md) | [仮引当の期限を5分から15分にしたら販売数が3割減った](https://zenn.dev/shinagawa_web/articles/provisional-hold-inventory-stockout) |
| [rate-limit-cannot-protect](rate-limit-cannot-protect/README.md) | [nginxでlimit_reqの上限内なのに504が増える](https://zenn.dev/shinagawa_web/articles/rate-limit-cannot-protect) |
| [read-committed-lost-update-patterns](read-committed-lost-update-patterns/README.md) | [トランザクション内でも在庫がずれる2つの原因](https://zenn.dev/shinagawa_web/articles/read-committed-lost-update-patterns) |
| [select-for-update-throughput-ceiling](select-for-update-throughput-ceiling/README.md) | [セール時だけ注文が詰まるのはFOR UPDATE中の処理](https://zenn.dev/shinagawa_web/articles/select-for-update-throughput-ceiling) |
| [transient-502-keepalive-reuse](transient-502-keepalive-reuse/README.md) | [nginxで502が稀に発生する原因はkeepalive接続](https://zenn.dev/shinagawa_web/articles/transient-502-keepalive-reuse) |
| [turborepo-cache-misses](turborepo-cache-misses/README.md) | — |
