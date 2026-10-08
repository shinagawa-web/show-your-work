# rate-limit-cannot-protect

Records what k6, nginx, the app and the kernel see when nginx (OSS) `limit_req` sits in front of an app that works on a fixed number of requests at a time. Every scenario starts from the same state and changes one item at 8 s, except `00-control`, which changes nothing. The common settings are in `nginx/base.conf`, `app/main.go`, `k6/load.js`, `docker-compose.yml` (the app's startup limit and delays, the backlog sysctls) and `run-all.sh` (the k6 values passed to every scenario), what each scenario changes is in `scenarios/<name>.env`, and the predictions are in `scripts/expect.py`.

## Run

`.github/workflows/rate-limit-cannot-protect.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

The workflow runs the scenarios in two jobs, `cause` (00-05) and `measure` (06-11). Each job puts `results/checks.txt` (each check with its prediction, the observed value and pass / fail), `results/timing.txt` and `results/00-environment.txt` in the job summary, fails when a check fails or a run is invalid, and uploads `results/`, one directory per scenario, as the `rate-limit-cannot-protect-results-<job>` artifact.

## Pinned versions

The images in `Dockerfile` and `docker-compose.yml`.
