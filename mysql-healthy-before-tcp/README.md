# mysql-healthy-before-tcp

Starts the official `mysql` image from an empty data directory with a Docker health check, and connects with the `mysql` CLI as soon as the container is healthy, from another container on the Compose network and from the host through the published port. It records when the entrypoint's temporary server and the real server log `ready for connections`, when Docker reports the container healthy, and when and how the first `SELECT 1` of the test returns. It does the same with a health check over TCP, and with a test that retries `SELECT 1` until it returns.

## Run

`.github/workflows/mysql-healthy-before-tcp.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

Each job runs `./run-all.sh <job>`, which runs the experiments listed for that job in `jobs.tsv`, three runs each, two at a time. Job names start with the article sections that use their numbers.

| Job | Experiment |
|---|---|
| `s2-s4-baseline` | `baseline`, with the health check comparison in its first run |
| `s5-tcp-healthcheck` | `tcp-healthcheck` |
| `s5-wait-for-query` | `wait-for-query` |

Each job writes `results/summary.md` to its job summary and uploads `results/` as the `mysql-healthy-before-tcp-<job>` artifact. The `report` job merges the artifacts, runs `scripts/summary.py` on all of them, and writes the merged `results/summary.md` to its job summary, to a comment on the open pull request of the branch (the same comment is overwritten on every run), and to the `mysql-healthy-before-tcp-report` artifact.

A run fails with a `RUN INVALID` line at the end of the summary when a run has no `port: 0` line (the data directory was not empty), no `port: 3306` line or no healthy event, a test wrote no output, an experiment has no runs, or a command of the health check comparison ran outside the phase it is meant for.

## Setup

`docker-compose.yml` defines three services:

- `mysql`: `MYSQL_ROOT_PASSWORD` and `MYSQL_DATABASE` set, `initdb/` mounted on `/docker-entrypoint-initdb.d`, port 3306 published on a port per run, and the health check of the baseline
- `tester`: the `mysql` image running `scripts/client.sh` against `mysql:3306` on the Compose network, started by `docker compose up -d tester` with `depends_on` `condition: service_healthy`
- `hostclient`: the `mysql` image with `network_mode: host`, idle until `run-all.sh` runs `scripts/client.sh` in it against `127.0.0.1:<published port>` right after `docker compose up -d --wait mysql` returns

`initdb/10-sleep.sh` is sourced by the entrypoint while its temporary server runs. Both tests run in the same run. `scripts/client.sh` sends `SELECT 1` with credentials, once, or every 100 ms until it returns (`CLIENT_MODE=poll`), and writes the start time, exit code and output of each attempt.

Each run starts with `docker compose down -v`. `docker events` for the run's Compose project is recorded from before the containers start.

## Experiments

Each experiment is `conditions/<experiment>.env`, and `conditions/<experiment>.yml` when it overrides `docker-compose.yml`.

| Experiment | What changes from `baseline` |
|---|---|
| `baseline` | Nothing |
| `tcp-healthcheck` | The health check command (`conditions/tcp-healthcheck.yml`) |
| `wait-for-query` | The test retries `SELECT 1` every 100 ms until it returns |

In the first run of `baseline`, `run-all.sh` runs each command in `HC_CHECKS` with `docker exec` in the `mysql` container, once after the entrypoint logs that it runs `/docker-entrypoint-initdb.d/10-sleep.sh` and once after the `port: 3306` line, and opens a TCP connection to the published port from `hostclient` at the same two points.

## Outputs

`results/<experiment>/run<N>/` holds `mysql.log` (the container log), `inspect.json`, `events.jsonl` (`docker events` as JSON), `health_status.txt` (`docker events --filter event=health_status` in its default format), `net.tsv` and `host.tsv` (one line per attempt of each test), and `docker-proxy.txt` (the `docker-proxy` processes for the published port). `results/baseline/run1/healthchecks/` holds the exit code and output of each command of the health check comparison. `results/environment.txt` and `results/docker-info.txt` hold the versions, `/etc/docker/daemon.json` and the `dockerd` command line, and `results/timing.tsv` the seconds per job.

## Pinned versions

The `mysql` image tag in `docker-compose.yml` and `run-all.sh`. `results/environment.txt` records its digest.

- The temporary server starts with `--skip-networking` and a socket: https://github.com/docker-library/mysql/blob/9b6c81572125a4ff828e3037c71940f3f8311978/9.7/docker-entrypoint.sh#L122-L127
- The entrypoint runs `/docker-entrypoint-initdb.d` while the temporary server runs, then stops it: https://github.com/docker-library/mysql/blob/9b6c81572125a4ff828e3037c71940f3f8311978/9.7/docker-entrypoint.sh#L384-L405
