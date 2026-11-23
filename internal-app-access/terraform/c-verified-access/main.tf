module "alb" {
  source          = "../modules/alb"
  name            = local.name
  internal        = true
  vpc_id          = module.base.vpc_id
  vpc_cidr        = module.base.vpc_cidr
  subnet_ids      = module.base.private_subnet_ids
  certificate_arn = module.base.certificate_arn
  apps            = local.apps
}

variable "idc_group_id" {
  type    = string
  default = "00000000-0000-0000-0000-000000000000"
}

variable "crowdstrike_tenant_id" {
  type    = string
  default = "00000000000000000000000000000000"
}

variable "crowdstrike_min_overall_score" {
  type    = number
  default = 50
}

resource "aws_verifiedaccess_trust_provider" "idc" {
  trust_provider_type      = "user"
  user_trust_provider_type = "iam-identity-center"
  policy_reference_name    = "idc"
}

resource "aws_verifiedaccess_trust_provider" "crowdstrike" {
  trust_provider_type        = "device"
  device_trust_provider_type = "crowdstrike"
  policy_reference_name      = "crowdstrike"

  device_options {
    tenant_id = var.crowdstrike_tenant_id
  }
}

resource "aws_verifiedaccess_instance" "this" {
  description = local.name
}

resource "aws_verifiedaccess_instance_trust_provider_attachment" "idc" {
  verifiedaccess_instance_id       = aws_verifiedaccess_instance.this.id
  verifiedaccess_trust_provider_id = aws_verifiedaccess_trust_provider.idc.id
}

resource "aws_verifiedaccess_instance_trust_provider_attachment" "crowdstrike" {
  verifiedaccess_instance_id       = aws_verifiedaccess_instance.this.id
  verifiedaccess_trust_provider_id = aws_verifiedaccess_trust_provider.crowdstrike.id
}

resource "aws_verifiedaccess_group" "employees" {
  verifiedaccess_instance_id = aws_verifiedaccess_instance.this.id

  policy_document = <<-CEDAR
    permit(principal, action, resource)
    when {
      context.idc.groups has "${var.idc_group_id}" &&
      context.crowdstrike.assessment.overall >= ${var.crowdstrike_min_overall_score}
    };
  CEDAR

  depends_on = [
    aws_verifiedaccess_instance_trust_provider_attachment.idc,
    aws_verifiedaccess_instance_trust_provider_attachment.crowdstrike,
  ]
}

resource "aws_security_group" "endpoint" {
  name   = "${local.name}-verified-access"
  vpc_id = module.base.vpc_id
}

resource "aws_vpc_security_group_egress_rule" "endpoint_to_alb" {
  security_group_id            = aws_security_group.endpoint.id
  referenced_security_group_id = module.alb.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_endpoint" {
  security_group_id            = module.alb.security_group_id
  referenced_security_group_id = aws_security_group.endpoint.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_verifiedaccess_endpoint" "app" {
  for_each                 = local.apps
  verified_access_group_id = aws_verifiedaccess_group.employees.id
  attachment_type          = "vpc"
  endpoint_type            = "load-balancer"
  application_domain       = each.value
  domain_certificate_arn   = module.base.certificate_arn
  endpoint_domain_prefix   = each.key
  security_group_ids       = [aws_security_group.endpoint.id]

  load_balancer_options {
    load_balancer_arn = module.alb.arn
    port              = 443
    protocol          = "https"
    subnet_ids        = module.base.private_subnet_ids
  }
}
