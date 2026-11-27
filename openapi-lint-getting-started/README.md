# openapi-lint-getting-started

This adds Redocly CLI to an existing OpenAPI definition of a small orders API, step by step: lint with no config, add a configurable rule, ignore the existing findings, lint one change at a time on top of the ignore file, and serve `GET /orders/{id}` from an Express server with and without its order access check.

## Run

`.github/workflows/openapi-lint-getting-started.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

The `s2-s6` job (named after the article sections that use its output) runs `./run-all.sh`, writes `results/summary.md` to the job summary and to a comment on the open pull request of the branch (the same comment is overwritten on every run), and uploads `results/` as the `openapi-lint-getting-started-results` artifact.

The job fails when the verification cannot be done (an install fails, no ignore file is generated, the section 4 lint does not pass, or the server does not start or answer). `results/summary.md` then holds a `RUN INVALID` line.

## Steps

`run-all.sh` runs these in order. Each lint runs in its own copy of `openapi.yaml` under `results/work/`, so the folder itself has no ignore file.

| Step | What it does |
|---|---|
| Section 2 | Lints `openapi.yaml` with no config file |
| Section 3 | Lints with `redocly.yaml`, then with the same file without `extends` |
| Section 4 | Generates `.redocly.lint-ignore.yaml` with `redocly.yaml`, then lints again with it |
| Section 5 | Applies one change from `scripts/patch.mjs` to the section 4 state and lints, also with `--format=github-actions`; then regenerates the ignore file after the first change and lints again |
| Section 6 | Starts `server/app.mjs` with the order access check, then with `SKIP_ORDER_ACCESS_CHECK=1`, and sends `GET /orders/{id}` with `curl` as each user |

`openapi.yaml` has these findings on purpose: an example value that does not match its schema, a duplicated `operationId`, a path parameter that is not defined, operations without `summary`, no `license` in `info`, and a snake_case property in `Order`.

The server has four users with fixed bearer tokens in `server/app.mjs`: A and C in one organization, B in another, and an administrator. It holds one order created by A.

## Outputs

Under `results/`:

| File | What it records |
|---|---|
| `section2-lint.txt`, `section2-lint.json` | Lint output with no config file |
| `section3-lint.txt`, `section3-lint.json` | Lint output with `redocly.yaml` |
| `section3-lint-without-extends.txt` | Lint output with `redocly.yaml` without `extends` |
| `section4-generate.txt` | Output of `--generate-ignore-file` |
| `section4-lint-ignore.yaml` | The generated ignore file |
| `section4-lint.txt`, `section4-lint.json` | Lint output with the ignore file |
| `section5-<case>.txt` | Lint output after the change of each case |
| `section5-<case>-github-actions.txt` | The same lint with `--format=github-actions` |
| `section5-regenerate-ignore.diff` | The ignore file before and after it is regenerated |
| `section6-<state>-<user>.txt` | The request, response status and body |
| `section6-<state>-server.log` | The server log |
| `summary.md` | The tables of the summary |

Every `.txt` lint output ends with the exit code of the command.

## Pinned versions

Redocly CLI in `package.json`, Express in `server/package.json`, both with lockfiles. Node in `.github/workflows/openapi-lint-getting-started.yml`.
