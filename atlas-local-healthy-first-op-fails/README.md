# atlas-local-healthy-first-op-fails

Starts `mongodb/mongodb-atlas-local` as a GitHub Actions service container from empty data, lets the runner wait for it to become healthy, and runs a first step against it with the Node.js driver or mongoose. It records when the runner saw the service healthy, when Docker reported it healthy, when the image's runner logged each start of mongod and mongot, and when and how each operation of the step returned. It does the same with credentials, with a shorter health check interval during the start period, with mongot killed by the OOM killer after healthy, and with the sample data loaded.

## Run

`.github/workflows/atlas-local-healthy-first-op-fails.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

Each job starts one service container and runs `./run-all.sh <job> test` (and `./run-all.sh mongoose seed` before it for `mongoose`) as the first steps after checkout and `actions/setup-node`, then `./run-all.sh <job> collect`. The job name is `<job>-run<N>`.

| Job | Runs | Service container | Test step |
|---|---|---|---|
| `wait` | 6 | image defaults | `scripts/test.mjs wait`: insert 100 documents, create a search index, wait until it is queryable, `$search` |
| `nowait` | 3 | image defaults | `scripts/test.mjs nowait`: the same, with `$search` right after the search index is created |
| `mongoose` | 5 | image defaults | a `seed` step (`scripts/mongoose-step.mjs seed`: 10 `Model.create`) and a `test` step (`scripts/mongoose-step.mjs test`: `Model.find`) |
| `auth` | 3 | `MONGODB_INITDB_ROOT_USERNAME` and `MONGODB_INITDB_ROOT_PASSWORD` | `scripts/test.mjs wait` with the credentials in the connection string |
| `health-start-interval` | 3 | `--health-start-interval 1s` | `scripts/test.mjs wait` |
| `mongot-oom` | 3 | `--memory 1g --memory-swap 1g` | kills mongot with the OOM killer, then `scripts/test.mjs wait` |
| `sample-data` | 1 | `MONGODB_LOAD_SAMPLE_DATA=true` | `scripts/test.mjs wait` |

Each job writes `results/summary.md` to its job summary and uploads `results/` as the `atlas-local-healthy-first-op-fails-<job>-run<N>` artifact. The `report` job merges the artifacts, saves the log of each job from the GitHub API as `runner-job.log` for the runner's wait lines, runs `scripts/summary.py` on all of them, and writes the merged `results/summary.md` to its job summary, to a comment on the open pull request of the branch (the same comment is overwritten on every run), and to the `atlas-local-healthy-first-op-fails-report` artifact. When the comment would be too long for GitHub, the comment stops before `## Not used for numbers`.

A run fails with an `## Invalid` section at the end of the summary when a run has no test output, no container log, no start time, no runner wait lines in its job log, or no final `server started` line of the image's runner, or when the image's runner did not log `waiting for user to cancel context` in time.

## Setup

The service container has `RUNNER_LOG_FILE=/dev/stdout` and `MONGOT_LOG_FILE=/dev/stdout`, so `docker logs` holds the image's runner and mongot logs next to the mongod log. Port 27017 is published on the runner host, and the steps connect to `mongodb://localhost:27017/?directConnection=true`.

`run-all.sh` gets the container from `SERVICE_CONTAINER` (`job.services.mongodb.id`) and the run number from `RUN`. The first step runs `npm ci` before the first operation, and `steps.env` records when each step started and when `npm ci` ended.

`collect` waits for the image's runner to log `waiting for user to cancel context`, then saves `docker logs -t`, `docker inspect`, `docker top`, and `docker events --since <container creation time>` for the container.

### mongoose

`scripts/model.mjs` defines the same model in both steps when the module loads, with one index (`{ name: { type: String, index: true } }`), and records the model's index events, `Model.init()`, `createCollection` through schema middleware, `unhandledRejection` and the process exit. `scripts/list-indexes.mjs` connects with another client while the seed runs, and runs `listIndexes` on `test.items` once, right after the seed process exits.

### mongot-oom

Before the test, `run-all.sh` sets `oom_score_adj` of mongod to -1000 through `sudo` on the host, starts `head -c <size> /dev/zero | tail` in the container with `docker exec -u root`, sets its `oom_score_adj` to -1000 before it allocates, and waits up to 30 s for `oom_kill` in the container's `memory.events` and for mongot to leave `docker top`. The size is the memory limit minus the RSS of the container's processes plus half the RSS of mongot. After the test it polls `.State.Health` every 2 s for up to 120 s, and saves `dmesg`.

## Local runs

`./run-all.sh [job[:runs] ...]` without `SERVICE_CONTAINER` starts the container with `docker run`, waits for healthy the way the runner does (`scripts/wait_healthy.py`: inspect, then sleep with the runner's backoff while the status is `starting`), and runs the same steps. `mongoose` also runs each run once more with mongoose `debug` on, into `results/mongoose/debug-run<N>/`.

## Outputs

`results/<job>/run<N>/` holds `test.jsonl` (one line per operation, with start and end time, result or error), `seed.jsonl` and `listindexes.jsonl` for `mongoose`, `container.log`, `inspect.json`, `events.jsonl`, `steps.env`, `docker-run.env`, and for `mongot-oom` `oom.env`, `memory.events.*`, `oom-poll.tsv`, `top-*.txt`, `health-poll.jsonl` and `dmesg.txt`. `runner-job.log` is added by the `report` job. `results/environment.txt` holds the versions and `results/timing.tsv` the seconds and conclusion per job.

## Pinned versions

The image digest in `.github/workflows/atlas-local-healthy-first-op-fails.yml` and `run-all.sh`, `mongodb` and `mongoose` in `package.json`. The credentials of `auth` are in both files too.
