# show-your-work

[English](README.md) | 日本語

各フォルダが1つの検証で、GitHub Actions の `.github/workflows/<フォルダ名>.yml` で動きます。リポジトリを fork して Actions を有効にし、ワークフローを実行してください。サマリーはジョブサマリーに、生の出力は artifact に出ます。

| 検証 | 記事 |
|---|---|
| [chunk-load-heuristic-freshness](chunk-load-heuristic-freshness/README.md) | [週1回のデプロイなら前のバージョンが17時間動く](https://zenn.dev/shinagawa_web/articles/chunk-load-heuristic-freshness) |
| [cpu-quota-throttling-p99](cpu-quota-throttling-p99/README.md) | [CPU使用率が低いのにp99だけ遅い原因はCPU上限](https://zenn.dev/shinagawa_web/articles/cpu-quota-throttling-p99) |
| [distroless-ephemeral-debug](distroless-ephemeral-debug/README.md) | — |
| [exit137-sender](exit137-sender/README.md) | — |
| [nodejs-eventloop-starvation-p99](nodejs-eventloop-starvation-p99/README.md) | [Node.jsのp99だけ悪化する原因はイベントループ詰まり](https://zenn.dev/shinagawa_web/articles/nodejs-eventloop-starvation-p99) |
| [provisional-hold-inventory-stockout](provisional-hold-inventory-stockout/README.md) | [仮引当の期限を5分から15分にしたら販売数が3割減った](https://zenn.dev/shinagawa_web/articles/provisional-hold-inventory-stockout) |
| [rate-limit-cannot-protect](rate-limit-cannot-protect/README.md) | — |
| [read-committed-lost-update-patterns](read-committed-lost-update-patterns/README.md) | [トランザクション内でも在庫がずれる2つの原因](https://zenn.dev/shinagawa_web/articles/read-committed-lost-update-patterns) |
| [select-for-update-throughput-ceiling](select-for-update-throughput-ceiling/README.md) | [FOR UPDATEのスループット上限はロック保持時間の逆数](https://zenn.dev/shinagawa_web/articles/select-for-update-throughput-ceiling) |
| [transient-502-keepalive-reuse](transient-502-keepalive-reuse/README.md) | [nginxで502が稀に発生する原因はkeepalive接続](https://zenn.dev/shinagawa_web/articles/transient-502-keepalive-reuse) |
