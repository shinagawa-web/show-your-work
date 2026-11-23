# eks-rds-iam-auth

This moves an app on EKS from a MySQL password in Secrets Manager (synced by External Secrets Operator, rotated by the AWS rotation function) to RDS IAM database authentication with EKS Pod Identity, and then to RDS Proxy with end-to-end IAM authentication. A client in the cluster keeps sending requests to the app and records each result, so that every step can be checked for failed requests.

## Run

There is no workflow for this folder. It runs from a machine with AWS credentials for the verification account, against an environment that stays up between runs:

```
AWS_PROFILE=<profile> AWS_REGION=ap-northeast-1 ./run-all.sh up
AWS_PROFILE=<profile> AWS_REGION=ap-northeast-1 ./run-all.sh
AWS_PROFILE=<profile> AWS_REGION=ap-northeast-1 ./run-all.sh down
```

`run-all.sh` takes the steps to run as arguments; with none it runs `token s2 s3 s4 s5 s6`. A step can be run again on its own.

| Step | What it does |
|---|---|
| `up` | VPC, EKS (one managed node, with the Pod Identity Agent add-on), RDS for MySQL, ECR repositories and a Secrets Manager VPC endpoint (`terraform/up`) |
| `token` | Builds an IAM authentication token with `BuildAuthToken` in a Pod that gets its credentials from Pod Identity (`app/tokencheck`) |
| `s2` | The setup before the migration: the app password in Secrets Manager, the `SecretsManagerRDSMySQLRotationSingleUser` rotation function, External Secrets Operator, Reloader, the app (`app/before`) and the load client (`app/loadgen`). Then one `rotate-secret` |
| `s3` | Enables IAM database authentication on the instance and creates the DB user `app_iam` with the same grants as `app` |
| `s4` | A role allowed `rds-db:connect` on the instance and `app_iam`, associated with the app ServiceAccount, and the app Pods recreated (`terraform/s4`) |
| `s5` | Switches the app to `app/after`, which builds a token for every new connection, then compares the connections `app_iam` opened on the DB with the tokens the app logged |
| `s6` | RDS Proxy with `default_auth_scheme = "IAM_AUTH"` (`terraform/s6`), then the role policy and `DB_HOST` moved to the proxy, with Amazon Root CA 1 added to the CA bundle |
| `reset` | Undoes `s3` to `s6` and deletes the app and the load client, so that `token s2 s3 s4 s5 s6` can run again from the start |
| `down` | Deletes everything |

Terraform state, the kubeconfig and the downloaded tools are kept under `state/` and `.bin/`.

## Outputs

Under `results/`, one folder per step, each with `run.log` (the output of the step) and `seconds`. The account ID is replaced with `111122223333`. `summary.md` is written after every run from the files of the steps that have run.

| File | What it records |
|---|---|
| `token/tokencheck.log` | The credential source, the caller ARN and the parts of the token, without the signature |
| `<step>/load.jsonl` | Each request of the load client during the step: time, result, Pod, DB user, connection ID |
| `<step>/load.json` | The window of the step and the number of requests and failures in it |
| `s2/events.json` | When `rotate-secret` was called, when `AWSCURRENT` moved, when the Kubernetes Secret changed and when the rollout finished |
| `s2/analysis.json` | Requests answered by the old Pods after the rotation, and the first request answered by a new Pod |
| `s2/rotation-lambda.log` | The log of the rotation function for this rotation |
| `s3/status.log` | The instance status while IAM authentication was enabled |
| `s4/pod-identity-env.jsonl` | The `AWS_*` variables that Pod Identity added to the recreated Pods |
| `s5/app.diff` | `diff -u app/before/main.go app/after/main.go` |
| `s5/processlist.log` | The connection IDs of `app_iam` in `information_schema.PROCESSLIST`, read every second during the step |
| `s5/connections.json` | The number of those connections and of `building auth token` lines in the app logs, over the same period |
| `s6/events.json` | When the role policy was moved to the proxy, when `DB_HOST` was switched and when the rollout finished |
| `s6/proxy.json` | The first request answered by a Pod connected through the proxy |

## Pinned versions

Terraform, kubectl, Helm, ko, the External Secrets Operator and Reloader charts, the rotation application and the MySQL client image are at the top of `run-all.sh`. The AWS provider is in each `terraform/*/main.tf`, Kubernetes and MySQL in `terraform/up/main.tf`, and the Go modules in `app/go.mod`.
