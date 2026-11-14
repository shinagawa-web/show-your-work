# iam-policy-scanners

This runs three ways of checking the IAM policies of a Terraform pull request (Checkov, Trivy, and IAM Access Analyzer through tf-policy-validator) on the same set of policies, one problem per policy, and records what each of them reports and the exit code that would fail a CI job. It also records how each handles a policy whose content Terraform only knows at apply time, and what CheckNoNewAccess returns for each policy against one reference policy.

## Run

`run-all.sh` writes `results/summary.md` and the raw output of every tool under `results/`.

Checkov and Trivy run without AWS credentials. `terraform plan` runs with fake static credentials and the AWS provider's `skip_*` settings, so it does not call AWS either. Only tf-policy-validator calls AWS: `access-analyzer:ValidatePolicy` and `access-analyzer:CheckNoNewAccess`. CheckNoNewAccess is charged per call. Nothing is applied.

## Policies

Each file under `terraform/cases/` is one policy, attached to the role in `terraform/common.tf`. `run-all.sh` puts `common.tf` and one case into a directory of its own, scans that directory with Checkov and Trivy, and runs `terraform plan` and `terraform show -json` in it for tf-policy-validator.

| Case | Policy |
|---|---|
| `baseline` | `s3:GetObject` on `arn:aws:s3:::app-bucket/*` in `aws_iam_policy` |
| `action-star` | Action `*` on `*` |
| `s3-star` | `s3:*` on `*` |
| `not-action` | `NotAction` `iam:*`, `organizations:*`, `account:*` on `*` |
| `passrole-star` | `iam:PassRole` on `*` |
| `passrole-star-inline` | `iam:PassRole` on `*` in `aws_iam_role_policy` |
| `passrole-app-path` | `iam:PassRole` on `role/app/*` |
| `passrole-app-path-condition` | `iam:PassRole` on `role/app/*` with an `iam:PassedToService` condition |
| `create-policy-version` | `iam:CreatePolicyVersion` on `*` |
| `action-typo` | `s3:GetObjcet` on `arn:aws:s3:::app-bucket/*` |
| `unknown-at-plan` | `passrole-star` plus a statement on the ARN of an S3 bucket created in the same configuration |
| `action-star-policy-document` | `action-star` written with `data.aws_iam_policy_document` |

All but `passrole-star-inline` and `action-star-policy-document` are written with `jsonencode()` in `aws_iam_policy`. `reference-policy.json` is the existing policy passed to CheckNoNewAccess.

## How each tool runs

| Tool | Command | Exit code |
|---|---|---|
| Checkov | `checkov -d <case directory> --framework terraform -o json` | 1 when a check fails |
| Trivy | `trivy config --checks-bundle-repository <pinned bundle> --exit-code 1 -f json <case directory>` | 1 when a check fails |
| tf-policy-validator | `validate` and `check-no-new-access --reference-policy reference-policy.json --reference-policy-type identity` on the plan JSON, through `tfpv.py` | 2 when an ERROR or SECURITY_WARNING finding is found, 1 on an exception |

`tfpv.py` runs tf-policy-validator unchanged and prints an `api-call` line for every Access Analyzer request with the result returned, so that the number of policies sent is counted from the requests.

The PyPI release of tf-policy-validator is also run once on one plan JSON, and its exit code and output are recorded.

## Outputs

Under `results/`. The account ID is replaced with `111122223333`.

| File | What it records |
|---|---|
| `summary.md` | The tables of the summary |
| `versions.txt` | The version of every tool, the AWS provider and the Trivy checks bundle digest |
| `<case>/plan.json` | `terraform show -json` of the plan |
| `<case>/checkov.json`, `checkov.rc` | Checkov output and exit code |
| `<case>/trivy.json`, `trivy.rc` | Trivy output and exit code |
| `<case>/validate.out`, `validate.rc` | tf-policy-validator `validate` log, findings and exit code |
| `<case>/check-no-new-access.out`, `check-no-new-access.rc` | The same for `check-no-new-access` |
| `tfpv-release.out`, `tfpv-release.rc` | The PyPI release of tf-policy-validator on `passrole-star/plan.json` |
| `duration.txt` | How long the run took |

## Pinned versions

Terraform, Checkov, Trivy, the Trivy checks bundle and tf-policy-validator are at the top of `run-all.sh`. The AWS provider is in `terraform/common.tf`.
