# file-relay-or-direct

This sets up an app on EC2, an S3 bucket and a CloudFront distribution in AWS, and records the HTTP status of an S3 presigned URL and a CloudFront signed URL issued by the app after the object is deleted from S3 while CloudFront has it cached, and of an S3 presigned URL after the credentials that signed it expire.

## Run

This folder has no workflow. The client has to be outside AWS, so `run-all.sh` runs on a host on the internet with the credentials of a role in an AWS account used only for this verification, and `results/` from that run is committed.

Without arguments, `run-all.sh` runs `up` (Terraform creates everything) and then `measure`, which runs `expiry-sign`, `access`, `deletion` and `expiry-check` in that order. It takes a little over 15 minutes, because `expiry-check` waits until the session credentials that signed the URL in `expiry-sign` have expired. Each of these can be given as an argument to run it alone; it replaces only its own file in `results/` and rewrites `results/summary.md`. The setup stays in place between runs. `down` runs only when given as an argument: Terraform destroys everything and `results/cleanup.log` lists what is left. The security group lets in only the public IP address the host has when `up` runs.

The role needs to create EC2 instances, VPCs, S3 buckets, CloudFront distributions, public keys and key groups, and IAM roles and instance profiles named `syw-file-relay-*`, and to assume `role/syw-file-relay-signer`.

## Setup

| Part | Where | What |
|---|---|---|
| App | `app/main.go` on one EC2 instance, run by systemd, HTTP on port 8080 | `GET /download` streams the body of `GetObject` into the response. `GET /sign/s3-get` returns an S3 presigned URL and `GET /sign/cf` a CloudFront signed URL. Keys under `private/` need the header `X-Allow: 1` |
| S3 | `terraform/main.tf` | One bucket with public access blocked. The app reaches it through a gateway endpoint in its VPC |
| CloudFront | `terraform/main.tf` | Reads the bucket with origin access control. `public/*` without signing, `private/*` and the default behavior with a key group. Cache policy `Managed-CachingOptimized` |
| IAM | `terraform/main.tf` | `syw-file-relay-app` for the instance. `syw-file-relay-signer`, assumed from the host for 900 seconds to sign one S3 presigned URL |
| Host | `run-all.sh` | Sends requests with `curl` and the AWS CLI |

`run-all.sh` exits non-zero when an access check does not return the status in `access_checks`, when a URL does not return 200 before the object is deleted or right after signing, when the object is not served from the CloudFront cache before it is deleted, when Terraform fails, or when something is left after `down`.

## Outputs

Under `results/`. The account ID is replaced with `111122223333`, the public IP address of the instance with `203.0.113.10`, the public IP address of the host with `198.51.100.10`, access key IDs with `ASIAIOSFODNN7EXAMPLE`, and session tokens in error bodies are removed. Bodies are recorded only for responses that are not 200 or are shorter than 200 bytes.

| File | What it records |
|---|---|
| `summary.md` | The tables of the summary |
| `environment.txt` | Instance type, AMI, availability zone, Go and SDK versions |
| `access.jsonl` | Status, `X-Cache` and body of each access check |
| `deletion.jsonl` | The same for the relay through the app and the two URLs, before and after the object is deleted and after the invalidation |
| `expiry.jsonl` | When the URL was signed, when the session credentials expire, and the responses |
| `terraform-destroy.log` | Terraform output of `down` |
| `cleanup.log` | What was left after `down` |
| `invalid.log` | Checks that made the run invalid, when there are any |

## Pinned versions

Terraform, Go and the instance type are at the top of `run-all.sh`. The AWS provider is in `terraform/main.tf`. The AWS SDK for Go is in `app/go.mod`.
