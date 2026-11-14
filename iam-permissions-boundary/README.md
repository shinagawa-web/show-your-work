# iam-permissions-boundary

This sets up an AWS account so that a developer can create IAM roles only with a permissions boundary, then records which developer commands are allowed or denied, with the error messages, and what a role the developer created can do.

## Run

`.github/workflows/iam-permissions-boundary.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. The job gets AWS credentials through OIDC, by assuming a role in the verification account. `run-all.sh` takes the credentials from the default chain and the account ID from `sts get-caller-identity`.

The `s3-s4` job (named after the article sections that use its output) runs `./run-all.sh`, writes `results/summary.md` to the job summary and to a comment on the open pull request of the branch (the same comment is overwritten on every run), and uploads `results/` as the `iam-permissions-boundary-results` artifact.

The summary has two tables: the commands run as the developer (section 3), and the commands run with the role the developer created (section 4).

## Roles and policies

| Who | Credentials | Policies |
|---|---|---|
| Administrator | The credentials `run-all.sh` starts with | Creates `DevBoundary` and `role/syw-developer`, and deletes everything at the end |
| Developer | `role/syw-developer`, assumed by the administrator with session name `developer` | `policies/developer.json` as an inline policy |
| Role created by the developer | `role/app/worker`, assumed by the administrator with session name `app` | `AdministratorAccess`, with `policies/dev-boundary.json` (`DevBoundary`) as its permissions boundary |

`111122223333` in `policies/developer.json` is replaced with the account ID at run time. The trust policies of `role/syw-developer` and of the roles under `/app/` allow the administrator's role.

## Steps

| Step | What it does |
|---|---|
| Setup | Stops with an error if roles or `DevBoundary` are left by an earlier run (they are deleted on exit). Creates `DevBoundary` and `role/syw-developer`, then retries assuming the developer role until it passes |
| Section 3 | As the developer, retries `create-role` with `--path /app/` and the boundary until it passes, then runs the commands that are expected to be allowed or denied |
| Terraform | As the developer, runs `terraform apply` and `terraform destroy` on `terraform/main.tf`, an `aws_iam_role` with `path = "/app/"` and `permissions_boundary`. The state is local |
| Tags | As the developer, runs `create-role` with `--tags`. If it is denied, adds `iam:TagRole` and `iam:UntagRole` to the `ReadAppRoles` statement and retries until it passes |
| Section 4 | As the administrator, assumes `role/app/worker`, retries `s3api list-buckets` until it passes, then runs `iam list-users` |
| Cleanup | On exit, deletes the roles under `/app/`, `role/syw-developer` and `DevBoundary`, and checks that none is left |

IAM changes take a while to be applied. A command that is denied before then cannot be told apart from a denial by the policies, so each step that expects a denial runs after a command expected to pass has passed.

`run-all.sh` exits non-zero when a command is allowed or denied differently from what it expects, when a denial message does not contain the expected wording, when Terraform fails, or when something is left after cleanup.

## Outputs

Under `results/`. The account ID is replaced with `111122223333`, and the name and session name of the administrator's role with `admin`.

| File | What it records |
|---|---|
| `setup.log` | Creating `DevBoundary` and `role/syw-developer` |
| `section3.log` | Each section 3 command, its output, and whether it matched what was expected |
| `section4.log` | The same for section 4 |
| `checks.jsonl` | One line per command of sections 3 and 4, from which `summary.md` is made |
| `terraform.log` | `terraform init`, `apply` and `destroy` run as the developer |
| `tagged-create-role.log` | `create-role` with `--tags`, and whether `iam:TagRole` had to be added |
| `waits.log` | Each retry until a command passed: the attempts and seconds |
| `cleanup.log` | What was left after cleanup |
| `summary.md` | The tables of the summary |

## Pinned versions

Terraform is at the top of `run-all.sh`. The AWS provider is in `terraform/main.tf`.
