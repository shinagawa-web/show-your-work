# internal-app-access

## Section 2: pricing assumptions

On-demand unit prices for ap-northeast-1 from the AWS Price List bulk files, fetched at 2026-11-23T01:13:15Z.

| Item | Unit | USD |
|---|---|--:|
| ALB | ALB-hour | 0.0243 |
| ALB LCU | LCU-hour | 0.008 |
| Public IPv4 address in use | IPv4 address-hour | 0.005 |
| Cognito SAML/OIDC federated user over the first 50 MAU | MAU | 0.015 |
| Verified Access application (first 148,800 hours) | App-hour | 0.35 |
| Verified Access data processing | GB | 0.02 |

Publication date of each price list file.

| Offer | publicationDate |
|---|---|
| AWSELB | 2026-11-19T22:27:09Z |
| AmazonVPC | 2026-11-19T20:55:45Z |
| AmazonCognito | 2026-11-19T18:39:22Z |

## Section 3: A, IP allowlist on the internet-facing ALB

`terraform plan` of `terraform/baseline` and of `terraform/a-ip-allowlist`, each from an empty state. The baseline holds the ALB, the Lambda app behind it, a VPC and a self-signed certificate, and every configuration has the same.

| Plan | Resources to add |
|---|--:|
| baseline | 31 |
| A | 32 |
| A minus baseline | 1 |

Resource types that A adds over the baseline.

| Resource type | Count |
|---|--:|
| `aws_vpc_security_group_ingress_rule` | 1 |

Monthly cost of A, estimated for 1 application from the unit prices in section 2 and `assumptions.json`. The resources above were counted in a plan with 3 applications.

| Item | Quantity | Unit | Unit price (USD) | Monthly (USD) |
|---|--:|---|--:|--:|
| ALB hours | 730 | ALB-hour | 0.0243 | 17.74 |
| ALB LCU (new connections) | 22.22 | LCU-hour | 0.008 | 0.18 |
| ALB public IPv4 addresses | 1,460 | IPv4 address-hour | 0.005 | 7.30 |
| Total | | | | 25.22 |

curl sent from the maintainer's host to the ALB of the applied A, before and after the allowed CIDR was narrowed.

| Request | HTTP status | Redirect to host | App body returned |
|---|--:|---|---|
| GET https://wiki.internal-app-access.example.com/ to the ALB from this host, with this host's egress IP (/32) as the only allowed CIDR | 200 |  | yes |
| GET https://wiki.internal-app-access.example.com/ to the ALB from this host, with 203.0.113.10/32 as the only allowed CIDR | none (curl exit 28) |  | no |

curl every 2 s while the allowed CIDR was replaced once, with the end of `terraform apply`'s rule destruction as 0 s.

| Attempt | Started (s) |
|---|--:|
| Last one that reached the app | -0.5 |
| First one that did not, followed by 44 more that did not | +1.5 |

Opening A in a browser.

| Setting | Measured | Reported | Inferred or not confirmed |
|---|---|---|---|
| Allowed CIDR is the egress IP of the maintainer's host (/32) | `/` reached the app at 2026-11-23T04:03:33Z | Opened by the user in a normal Chrome window |  |
| Allowed CIDR is 203.0.113.10/32 only | curl from the maintainer's host at 2026-11-23T02:12:49Z, with the same rule: exit 28 (timeout) | An agent opened it in a separate Chrome profile from 2026-11-23T02:13:06Z to 02:14:07Z: blank for 60 s, no body, no error page seen |  |

## Section 4: B, ALB authentication with Cognito and Google Workspace SAML

`terraform plan` of `terraform/baseline` and of `terraform/b-alb-cognito`, each from an empty state. The baseline holds the ALB, the Lambda app behind it, a VPC and a self-signed certificate, and every configuration has the same.

| Plan | Resources to add |
|---|--:|
| baseline | 31 |
| B | 37 |
| B minus baseline | 6 |

Resource types that B adds over the baseline.

| Resource type | Count |
|---|--:|
| `aws_cognito_identity_provider` | 1 |
| `aws_cognito_user_pool` | 1 |
| `aws_cognito_user_pool_client` | 1 |
| `aws_cognito_user_pool_domain` | 1 |
| `aws_vpc_security_group_egress_rule` | 1 |
| `aws_vpc_security_group_ingress_rule` | 1 |

Monthly cost of B, estimated for 1 application from the unit prices in section 2 and `assumptions.json`. The resources above were counted in a plan with 3 applications.

| Item | Quantity | Unit | Unit price (USD) | Monthly (USD) |
|---|--:|---|--:|--:|
| ALB hours | 730 | ALB-hour | 0.0243 | 17.74 |
| ALB LCU (new connections) | 22.22 | LCU-hour | 0.008 | 0.18 |
| ALB public IPv4 addresses | 1,460 | IPv4 address-hour | 0.005 | 7.30 |
| Cognito SAML users over the free tier | 50 | MAU | 0.015 | 0.75 |
| Total | | | | 25.97 |

curl sent from the maintainer's host to the ALB of the applied B without a session. The second request followed the first redirect once, while the identity provider still had the dummy SAML metadata.

| Request | HTTP status | Redirect to host | App body returned |
|---|--:|---|---|
| GET https://wiki.internal-app-access.example.com/ to the ALB without a session cookie | 302 | employee-apps-48f69f63.auth.us-east-1.amazoncognito.com | no |
| GET the Location of the 302 above, once | 302 | accounts.google.com | no |

Signing in to B in a browser, with the SAML metadata of a Google Workspace application.

| Setting | Measured | Reported | Inferred or not confirmed |
|---|---|---|---|
| Google Workspace SAML metadata in the identity provider | `/` reached the app at 2026-11-23T01:03:24Z | Signed in by the user; auth_time 2026-11-23T01:03:23Z |  |
| Same | `/` reached the app at 2026-11-23T04:08:34Z; auth_time 2026-11-23T04:08:33Z; referer https://accounts.google.com/, sec-fetch-site cross-site | Signed in by the user | Kind of window, and whether anything was typed on the Google pages, not confirmed |

Headers the app received from the ALB after the sign-ins above.

| Header | Contents |
|---|---|
| `x-amzn-oidc-data` | `sub`, `email_verified`, `identities`, `email`, `custom:groups`, `username`, `exp`, `iss` |
| `x-amzn-oidc-accesstoken` | present, value not recorded |
| `x-amzn-oidc-identity` | present, value not recorded |

## Section 5: C, Verified Access in front of an internal ALB

`terraform plan` of `terraform/baseline` and of `terraform/c-verified-access`, each from an empty state. The baseline holds the ALB, the Lambda app behind it, a VPC and a self-signed certificate, and every configuration has the same.

| Plan | Resources to add |
|---|--:|
| baseline | 31 |
| C | 45 |
| C minus baseline | 14 |

Resource types that C adds over the baseline.

| Resource type | Count |
|---|--:|
| `aws_acm_certificate` | 1 |
| `aws_acm_certificate_validation` | 1 |
| `aws_security_group` | 1 |
| `aws_verifiedaccess_endpoint` | 3 |
| `aws_verifiedaccess_group` | 1 |
| `aws_verifiedaccess_instance` | 1 |
| `aws_verifiedaccess_instance_trust_provider_attachment` | 2 |
| `aws_verifiedaccess_trust_provider` | 2 |
| `aws_vpc_security_group_egress_rule` | 1 |
| `aws_vpc_security_group_ingress_rule` | 1 |

The same plan with the device trust provider left off the Verified Access instance (`attach_device_trust = false`).

| Plan | Resources to add |
|---|--:|
| C without the device trust provider | 44 |
| C without the device trust provider minus baseline | 13 |

In the applied C, 3 Verified Access endpoints of type load-balancer were active and all pointed to 1 internal ALB.

Monthly cost of C, estimated for 1 application from the unit prices in section 2 and `assumptions.json`. The resources above were counted in a plan with 3 applications.

| Item | Quantity | Unit | Unit price (USD) | Monthly (USD) |
|---|--:|---|--:|--:|
| ALB hours | 730 | ALB-hour | 0.0243 | 17.74 |
| ALB LCU (new connections) | 22.22 | LCU-hour | 0.008 | 0.18 |
| Verified Access applications | 730 | App-hour | 0.35 | 255.50 |
| Verified Access data processing | 10 | GB | 0.02 | 0.20 |
| Total | | | | 273.62 |

curl sent from the maintainer's host to the three Verified Access endpoints and to the internal ALB of the applied C, without signing in.

| Request | HTTP status | Redirect to host | App body returned |
|---|--:|---|---|
| GET https://wiki.internal-app-access.example.com/ to the Verified Access endpoint of wiki, without signing in | 303 | us-east-1.signin.aws | no |
| GET https://admin.internal-app-access.example.com/ to the Verified Access endpoint of admin, without signing in | 303 | us-east-1.signin.aws | no |
| GET https://expense.internal-app-access.example.com/ to the Verified Access endpoint of expense, without signing in | 303 | us-east-1.signin.aws | no |
| GET https://wiki.internal-app-access.example.com/ to the internal ALB from this host | none (curl exit 28) |  | no |

Opening C (wiki) in a browser.

| Setting | Measured | Reported | Inferred or not confirmed |
|---|---|---|---|
| Device trust provider attached, policy allows the verifiers group | no request reached the app (Lambda log) | After signing in to IAM Identity Center the user's browser stopped at /validate_domain on a Verified Access domain: 200, body Redirecting... (14 bytes) | Cause not confirmed |
| Device trust provider detached, policy allows the verifiers group | `/` reached the app at 2026-11-23T01:47:29Z | Opened by an agent in the user's Chrome, where a session was still active; the sign-in page was not shown. The app body came back with 200 |  |
| Same | `/` reached the app at 2026-11-23T04:05:54Z; sec-fetch-site none | Opened by the user in a normal Chrome window | A session that was still active was used; whether the sign-in page was shown is not known |
| Same | `/` reached the app at 2026-11-23T04:07:05Z; referer https://us-east-1.signin.aws/, sec-fetch-site cross-site, no Cookie header | Opened by the user | The user went through the sign-in page; kind of window not confirmed |
| Device trust provider detached, policy allows only a group ID that does not exist | no request reached the app (Lambda log) | An agent opened it in the user's Chrome, where a session was still active, at about 2026-11-23T01:57:30Z: 403, body 403 Unauthorized, for / and /favicon.ico |  |

Headers the app received through Verified Access in the rows above where the request reached the app.

| Header | Contents |
|---|---|
| `x-amzn-ava-user-context` | `user.user_id`, `user.user_name`, `user.email.address`, `user.email.verified` |

## Section 7: comparison

Resources each plan adds over the baseline plan, counted with 3 applications, and the monthly totals from sections 3 to 5, estimated for 1 application.

| Configuration | Resources added over the baseline | Monthly (USD) |
|---|--:|--:|
| A: IP allowlist on the internet-facing ALB | 1 | 25.22 |
| B: ALB authentication with Cognito and Google Workspace SAML | 6 | 25.97 |
| C: Verified Access in front of an internal ALB | 14 | 273.62 |
