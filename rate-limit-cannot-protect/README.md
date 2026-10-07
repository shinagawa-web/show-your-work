# rate-limit-cannot-protect

Records what k6, nginx, the downstream and the kernel see when nginx (OSS) `limit_req` sits in front of a downstream that works on a fixed number of requests at a time. Every scenario starts from the same state and changes one item at 8 s, except `00-control`, which changes nothing. The common settings are in `nginx/base.conf`, `downstream/main.go`, `k6/load.js`, `docker-compose.yml` (the downstream's startup limit and delays, the backlog sysctls) and `scripts/run-all.sh` (the k6 values passed to every scenario), what each scenario changes is in `scenarios/<name>.env`, and the predictions are in `scripts/expect.py`.

## Run

On a Linux host with BTF (for bpftrace), Docker, Docker Compose v2 and `python3` (this is what CI does). On macOS, `scripts/vm-run.sh` runs the same inside the Lima VM from `lima.yaml`.

```
./scripts/run-all.sh                    # every scenario
./scripts/run-all.sh 02-b-latency       # some of them
```

Raw output goes to `results/`, one directory per scenario. `results/checks.txt` has each check with its prediction, the observed value and pass / fail.
