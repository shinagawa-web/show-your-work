module "alb" {
  source          = "../modules/alb"
  name            = local.name
  internal        = false
  vpc_id          = module.base.vpc_id
  vpc_cidr        = module.base.vpc_cidr
  subnet_ids      = module.base.public_subnet_ids
  certificate_arn = module.base.certificate_arn
  apps            = local.apps
}

resource "aws_vpc_security_group_ingress_rule" "office" {
  for_each          = toset(var.office_cidrs)
  security_group_id = module.alb.security_group_id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

variable "office_cidrs" {
  type    = list(string)
  default = ["203.0.113.10/32"]
}
