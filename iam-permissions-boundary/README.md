# iam-permissions-boundary

This sets up an AWS account so that a developer can create IAM roles only with a permissions boundary, then records which developer commands are allowed or denied, with the error messages, and what a role the developer created can do.

## Run

`.github/workflows/iam-permissions-boundary.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. The job gets AWS credentials through OIDC, by assuming a role in the verification account.

The `s3-s4` job (named after the article sections that use its output) runs `./run-all.sh`, writes `results/summary.md` to the job summary and to a comment on the open pull request of the branch (the same comment is overwritten on every run), and uploads `results/` as the `iam-permissions-boundary-results` artifact.

## Roles and policies

| Who | Credentials | Policies |
|---|---|---|
| Administrator | The credentials `run-all.sh` starts with | Creates `DevBoundary` and `role/syw-developer`, and deletes everything at the end |
| Developer | `role/syw-developer`, assumed by the administrator with session name `developer` | `policies/developer.json` as an inline policy |
| Role created by the developer | `role/app/worker`, assumed by the administrator with session name `app` | `AdministratorAccess`, with `policies/dev-boundary.json` (`DevBoundary`) as its permissions boundary |

`run-all.sh` exits non-zero when a command is allowed or denied differently from what it expects, when a denial message does not contain the expected wording, when Terraform fails, or when something is left after cleanup.

## Outputs

Under `results/`. The account ID is replaced with `111122223333`.

| File | What it records |
|---|---|
| `summary.md` | The tables of the summary |
| `section3.log` | Each section 3 command, its output, and whether it matched what was expected |
| `section4.log` | The same for section 4 |
| `terraform.log` | `terraform init`, `apply` and `destroy` run as the developer |
| `tagged-create-role.log` | `create-role` with `--tags`, its output, and whether it passed |
| `waits.log` | Each retry until a command passed: the attempts and seconds |
| `cleanup.log` | What was left after cleanup |

## Pinned versions

Terraform is at the top of `run-all.sh`. The AWS provider is in `terraform/main.tf`.
