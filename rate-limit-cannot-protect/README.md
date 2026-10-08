# rate-limit-cannot-protect

Records what k6, nginx, the app and the kernel see when nginx (OSS) `limit_req` sits in front of an app that works on a fixed number of requests at a time. Every scenario starts from the same state and changes one item at 8 s, except `00-control`, which changes nothing. The common settings are in `nginx/base.conf`, `app/main.go`, `k6/load.js`, `docker-compose.yml` (the app's startup limit and delays, the backlog sysctls) and `run-all.sh` (the k6 values passed to every scenario), what each scenario changes is in `scenarios/<name>.env`, and the predictions are in `scripts/expect.py`.

## Run

On a Linux host with BTF (for bpftrace), Docker, Docker Compose v2 and `python3` (this is what CI does).

```
cd rate-limit-cannot-protect
./run-all.sh                    # every scenario
./run-all.sh 02-b-latency       # some of them
```

Raw output goes to `results/`, one directory per scenario. `results/checks.txt` has each check with its prediction, the observed value and pass / fail.

On macOS, create the VM once with `limactl create --name=rate-limit-cannot-protect rate-limit-cannot-protect/lima.yaml` and `limactl start rate-limit-cannot-protect`, then run `scripts/vm-run.sh rate-limit-cannot-protect [scenario ...]` from the repository root. It copies this folder into the VM, runs `./run-all.sh` there and copies `results/` back.
