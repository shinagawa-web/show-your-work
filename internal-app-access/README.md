# internal-app-access

This creates three ways to show an internal web application on AWS only to employees, records how many resources each one adds to the same ALB, and checks with curl and in a browser who is stopped and who gets through. Next to it, it computes the monthly cost of each from the on-demand unit prices of the AWS Price List for ap-northeast-1.

- A: the security group of an internet-facing ALB allows only the office egress IP address
- B: the HTTPS listener of an internet-facing ALB authenticates with a Cognito user pool that has Google Workspace as its SAML identity provider
- C: three Verified Access endpoints, one per application, all pointing to the same internal ALB, with IAM Identity Center as the user trust provider and CrowdStrike as the device trust provider

## Run

There is no workflow. The scripts run on the maintainer's machine with `AWS_PROFILE=sre-verify-internal-app-access`, a member account used only for this folder. The Terraform configurations create everything in us-east-1, where the IAM Identity Center instance of the organization is. The scripts set the region themselves.

They use `aws` (AWS CLI v2), `curl`, `unzip`, `tar`, `python3` and `od`. `run-all.sh` downloads Terraform into `.bin/` and keeps the providers in `.bin/plugin-cache`, shared by the configurations, which are initialized one at a time.

- `./run-all.sh` plans, then applies, checks with curl and destroys each configuration. `./run-all.sh <configuration> ...` runs only those. It deletes the results of the configurations it runs
- `KEEP="<configuration> ..."` leaves the named configurations running after their checks
- It stops before it touches `results/` or AWS when a configuration already has resources in its state, unless the configuration is named in `ALLOW_EXISTING_STATE`
- `APP_DOMAIN_SUFFIX` sets the application domains. C needs a domain in public DNS: `terraform -chdir=terraform/c-verified-access apply -target=aws_acm_certificate.public` first, then the validation CNAME from the `certificate_validation_records` output, then the rest, then a CNAME from each application domain to its endpoint domain
- `VERIFIERS_GROUP_ID` is the IAM Identity Center group the policy of C allows
- `scripts/plan-counts.sh` only plans each configuration from an empty state in a temporary copy
- `python3 scripts/summary.py results .` rewrites `results/summary.md`

It exits with a non-zero code when an apply or destroy fails, when resources of the kinds it creates are in the account after it ends, or when a value to mask is left in `results/`.

`results/` is committed. It holds the results made on the maintainer's machine at the time of the commit that last changed it. The configurations were changed and applied again while the checks were made, so the logs of an apply under `results/<configuration>/` are not all from a first apply; `results/plans/` is, and the counts come from it.

## Sign-in checks

These are made by hand on configurations left running with `KEEP`.

1. B: pass the SAML metadata file of a Google Workspace application with `TF_VAR_saml_metadata_file`. The ACS URL is `https://<cognito_domain output>/saml2/idpresponse` and the entity ID is `urn:amazon:cognito:sp:<user_pool_id output>`. Open `https://<alb_dns_name output>/`
2. C: open `https://wiki.<APP_DOMAIN_SUFFIX>/`. Change the policy with `TF_VAR_idc_group_id`, and the device trust provider with `TF_VAR_attach_device_trust`
3. A: `scripts/block-delay.py` sends curl every 2 s while a saved plan replaces the allowed CIDR
4. `scripts/login-record.py` takes the requests between `--since` and `--until` from the log of the Lambda function behind the ALB and writes them to `results/login/<configuration>/`, with tokens, cookies, client addresses, user IDs and names, e-mail addresses and group names replaced
5. `results/login/observations.json` holds what was reported or inferred for each record. `scripts/summary.py` takes the measured part from the record

## Configurations

`terraform/modules/alb` is the ALB every configuration uses: a security group, the ALB, one Lambda target group per application, an HTTPS listener, and one listener rule per application on its host name. The Lambda function (`terraform/modules/alb/app`) returns and logs the request headers it receives. `internal` selects the scheme. With `cognito` set, each listener rule authenticates with Cognito before forwarding. `default_app` receives requests for other host names, so that the ALB DNS name can be opened without DNS records.

`terraform/modules/base` is used by every configuration too: a VPC with two public and two private subnets in two Availability Zones, an internet gateway, and a self-signed certificate imported into ACM for the ALB listener.

| Folder | What it adds to the two modules |
|---|---|
| `terraform/baseline` | Nothing. The internet-facing ALB on the public subnets |
| `terraform/a-ip-allowlist` | An ingress rule on 443 from `office_cidrs` |
| `terraform/b-alb-cognito` | Ingress on 443 from anywhere, egress on 443, the user pool, the SAML identity provider, the user pool domain and the app client |
| `terraform/c-verified-access` | The ALB is internal on the private subnets. A public ACM certificate, two trust providers, the Verified Access instance with both attached, a group with a Cedar policy, a security group for the endpoints, and three endpoints of type `load-balancer` on the ALB |

The default SAML metadata, the IAM Identity Center group ID and the CrowdStrike tenant ID are dummies. A dummy that AWS accepts shows only that AWS accepts its form.

## Outputs

`results/plans/<plan>/` holds `plan.log` and `resources.json`. For each configuration, `results/<configuration>/` holds the logs of apply and destroy, `alb.json` (the ALB scheme and how many of its network interfaces have a public IPv4 address) and `probes/` (each curl check with its prediction). `results/c-verified-access/recreate/` holds C after it was applied again. `results/login/` holds the sign-in checks. `results/prices/` holds the Price List files and `prices.json`, the unit prices taken from them. `results/costs.json` holds the cost of each configuration, with the LCU of each dimension.

## Cost model

`assumptions.json` holds the traffic, the number of employees and the hours in a month. `scripts/summary.py` takes the resources from `results/plans/`:

- Each ALB: its hours, and its LCU-hours from the largest of the four dimensions, with every request opening a new connection that stays open for `connection_seconds`, the processed bytes spread over the month, and the rule evaluations over the 10 free rules
- The public IPv4 addresses of the ALB, as counted in `alb.json`
- A SAML identity provider: every employee as a federated monthly active user, over the free tier in the price list
- Verified Access: the hours of each endpoint, and the processed bytes

## Pinned versions

`run-all.sh` pins Terraform. `terraform/*/common.tf` pins the AWS, TLS and archive providers, and `terraform/*/.terraform.lock.hcl` holds their hashes.
