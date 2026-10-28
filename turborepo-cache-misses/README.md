# turborepo-cache-misses

This builds a small pnpm workspace and, for one kind of change at a time, runs `turbo run test` with a cache built on `main` and records whether the test that the change breaks runs. Next to it, it records what a reader can look at on their own CI: the `cache hit`, `cache miss` and `cache bypass` lines and their hashes, the `dependencies`, `inputs` and `environmentVariables` of `b#test` in `turbo run test --dry=json`, `cache.sha` in the Run Summary, the `cache hit produced by sha:` line of `-v`, and the `::group::` lines printed when `GITHUB_ACTIONS` is set. As the correct answer, it runs `vitest run` in `packages/b` without Turborepo.

## Run

`.github/workflows/turborepo-cache-misses.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

Each job runs `./run-all.sh <job>`, which runs the conditions whose `job` column in `conditions.tsv` is that job. A condition that continues from a condition of another job (`after`) runs that condition first in the same job, and its results are left out of the job's `results/`.

| Job | Conditions |
|---|---|
| `s3-deps` | P1 to P3 |
| `s3-main` | P4 to P6 (they continue from P2) |
| `s3-files` | P7, P8 |
| `s3-env` | P9, P10 |

Each job writes `results/summary.md` to the job summary and uploads `results/` as the `turborepo-cache-misses-<job>` artifact. The `report` job merges the artifacts, runs `scripts/summary.mjs` on all of them, and writes the merged `results/summary.md` to its job summary and to a comment on the pull request of the branch, replacing the comment from the previous run.

A run fails with a `RUN INVALID` line at the end of the summary when the cache built on `main` did not pass, or the full `vitest run` of a condition did not fail exactly the test file the condition breaks.

## Workspace

`workspace/` is the repository every condition starts from. It is copied to its own directory per condition, with its own Git repository and its own `.turbo/cache`.

- `packages/a`: `src/math.ts`, exported as TypeScript source (`./src/*.ts`), no build script
- `packages/b`: depends on `a`. `src/total.ts` calls `add` from `a`. Three tests in `packages/b/test/`: `uses-a.test.ts` (uses `total`), `fixture-root.test.ts` (reads `fixtures/shared.json` at the root with `fs`), `env-mode.test.ts` (prints `APP_MODE` and fails when it is `new`)
- `packages/c`: one test, depends on nothing in the workspace
- `fixtures/shared.json`: read by `packages/b/test/fixture-root.test.ts`

The root `package.json` does not depend on any workspace package. `turbo.json` declares `test` with no `dependsOn`, no `env`, no `passThroughEnv` and no `inputs`.

The variable is `APP_MODE` because Vitest sets `process.env.MODE` to its own mode inside tests.

## Conditions

`conditions.tsv` has one row per condition.

- `after`: the condition starts from a copy of the directory of that condition, with its Git repository and `.turbo/cache`, instead of a fresh copy of `workspace/`
- `setup`: changes to `turbo.json` committed on `main` before the cache is built (`setup_*` in `scripts/changes.sh`)
- `change`: the change committed on the pull request branch (`change_*` in `scripts/changes.sh`)
- `mode`: `pr` commits the change on a branch and runs there; `pr-force` also runs `turbo run test --force`; `main` moves to a branch standing in for `main` with the pull request merged and runs there, also with `-v` and with `GITHUB_ACTIONS=true`; `main-cleared` does the same after deleting `.turbo/cache`, without the two extra runs
- `app_mode`: `APP_MODE` in the shell for the runs after the change. The cache on `main` is always built with `APP_MODE=old`
- `target`: the test file in `packages/b/test/` the change breaks

For each condition without `after`, `scripts/cond.sh` commits `setup` on `main`, builds the cache there with `turbo run test`, and records the commit. Then it runs:

| Name | Command |
|---|---|
| `dry` | `turbo run test --dry=json` |
| `cache` | `turbo run test --summarize` |
| `force` (`pr-force`) | `turbo run test --summarize --force` |
| `cache-verbose` (`main`) | `turbo run test --summarize -v` |
| `cache-gha` (`main`) | `turbo run test --summarize` with `GITHUB_ACTIONS=true` |
| `correct-b` | `vitest run` in `packages/b` |

## Outputs

`results/<condition>/` holds, for each command, `<name>.log` (the command and its output), `<name>.rc` (its exit code), and for `turbo run`, `<name>.summary.json` (the Run Summary). `correct-b.json` is the Vitest JSON report. `cache-build.sha` is the commit the cache was built on, `head.sha` is the commit the runs were made on, `turbo.json` is the configuration in use, and `change.stat` is `git show --stat` of the change. `results/versions.txt` holds the versions in use.

## Pinned versions

`scripts/versions.env` pins the Node.js image and pnpm. The workspace dependencies are pinned in `workspace/package.json`, `workspace/packages/*/package.json` and `workspace/pnpm-lock.yaml`.
