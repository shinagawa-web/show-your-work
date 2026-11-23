# internal-app-access

This creates, one after another, three ways to show an internal web application on AWS only to employees, records how many resources each one creates over the same ALB, and destroys each one before the next. Next to it, it computes the monthly cost of each from the on-demand unit prices of the AWS Price List for ap-northeast-1.

- A: the security group of an internet-facing ALB allows only the office egress IP address
- B: the HTTPS listener rules of an internet-facing ALB authenticate with a Cognito user pool that has Google Workspace as its SAML identity provider
- C: three Verified Access endpoints, one per application, all pointing to the same internal ALB, with IAM Identity Center as the user trust provider and CrowdStrike as the device trust provider

## Run

There is no workflow. `./run-all.sh` runs on the maintainer's machine with `AWS_PROFILE=sre-verify-internal-app-access`, a member account used only for this folder. The Terraform configurations create everything in us-east-1, where the IAM Identity Center instance of the organization is. `run-all.sh` sets the region itself.

It uses `aws` (AWS CLI v2), `curl`, `unzip`, `python3` and `od`. It downloads Terraform into `.bin/` and keeps the providers in `.bin/plugin-cache`, shared by the four configurations, which are initialized one at a time.

It stops with a non-zero exit code, after destroying what it created, when an apply or destroy fails, when the account has resources of the kinds it creates before it starts or after it ends, or when `scripts/summary.py` finds the results inconsistent. `results/summary.md` is what goes to the pull request.

## Configurations

`terraform/modules/alb` is the ALB every configuration uses: a security group with egress to the targets, the ALB, one target group per application, an HTTPS listener with a fixed 404 response, and one listener rule per application on its host name. `internal` selects the scheme. With `cognito` set, each listener rule authenticates with Cognito before forwarding.

`terraform/modules/base` is used by every configuration too: a VPC with two public and two private subnets in two Availability Zones, an internet gateway, and a self-signed certificate for `*.internal-app-access.example.com` imported into ACM. The ALB listener and the Verified Access endpoints use that certificate. No DNS records are created.

| Folder | What it adds to the two modules |
|---|---|
| `terraform/baseline` | Nothing. The internet-facing ALB on the public subnets |
| `terraform/a-ip-allowlist` | An ingress rule on 443 from `office_cidrs` |
| `terraform/b-alb-cognito` | Ingress on 443 from anywhere, egress on 443 to the identity provider endpoint, the user pool, the SAML identity provider from `google-saml-metadata.xml.tftpl`, the user pool domain and the app client |
| `terraform/c-verified-access` | The ALB is internal on the private subnets. Two trust providers, the Verified Access instance with both attached, a group with a Cedar policy, a security group for the endpoints, and three endpoints of type `load-balancer` on the ALB |

The SAML metadata, the IAM Identity Center group ID and the CrowdStrike tenant ID are dummies. The metadata carries the self-signed certificate as its signing certificate.

## Outputs

For each configuration, `results/<configuration>/` holds `init.log`, `apply.log` and `destroy.log`, `resources.json` (the managed resources in the state after apply, with the attributes the cost uses), and `alb.json` (the ALB scheme from `aws elbv2 describe-load-balancers` and how many of its network interfaces have a public IPv4 address). `results/c-verified-access/endpoints.json` is `aws ec2 describe-verified-access-endpoints` after apply.

`results/timings.tsv` holds the seconds of each apply and destroy. `results/leftovers-before.log` and `results/leftovers.log` list the Verified Access resources, load balancers, user pools, certificates and the VPC in the account before and after the run. `results/prices/` holds the Price List files and `prices.json`, the unit prices taken from them. `results/costs.json` holds the cost of each configuration, with the LCU of each dimension. The account ID is replaced with `111122223333` in everything under `results/`.

## Cost model

`assumptions.json` holds the traffic, the number of employees and the hours in a month. `scripts/summary.py` takes the resources from `resources.json`:

- Each ALB: its hours, and its LCU-hours from the largest of the four dimensions, with every request opening a new connection that stays open for `connection_seconds`, the processed bytes spread over the month, and the rule evaluations over the 10 free rules
- An internet-facing ALB: one public IPv4 address per subnet
- A SAML identity provider: every employee as a federated monthly active user, over the free tier in the price list
- Verified Access: the hours of each endpoint, and the processed bytes

## Pinned versions

`run-all.sh` pins Terraform. `terraform/*/common.tf` pins the AWS and TLS providers, and `terraform/*/.terraform.lock.hcl` holds their hashes.
