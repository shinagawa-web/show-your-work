module "alb" {
  source          = "../modules/alb"
  name            = local.name
  internal        = false
  vpc_id          = module.base.vpc_id
  vpc_cidr        = module.base.vpc_cidr
  subnet_ids      = module.base.public_subnet_ids
  certificate_arn = module.base.certificate_arn
  apps            = local.apps

  cognito = {
    user_pool_arn       = aws_cognito_user_pool.employees.arn
    user_pool_client_id = aws_cognito_user_pool_client.alb.id
    user_pool_domain    = aws_cognito_user_pool_domain.this.domain
  }
}

variable "cognito_domain_prefix" {
  type = string
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = module.alb.security_group_id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "idp" {
  security_group_id = module.alb.security_group_id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_cognito_user_pool" "employees" {
  name           = "employees"
  user_pool_tier = "ESSENTIALS"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }
}

resource "aws_cognito_identity_provider" "google" {
  user_pool_id  = aws_cognito_user_pool.employees.id
  provider_name = "GoogleWorkspace"
  provider_type = "SAML"

  provider_details = {
    MetadataFile = templatefile("${path.module}/google-saml-metadata.xml.tftpl", {
      certificate = join("", slice(split("\n", trimspace(module.base.certificate_pem)), 1, length(split("\n", trimspace(module.base.certificate_pem))) - 1))
    })
  }

  attribute_mapping = {
    email = "email"
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  user_pool_id = aws_cognito_user_pool.employees.id
  domain       = var.cognito_domain_prefix
}

resource "aws_cognito_user_pool_client" "alb" {
  user_pool_id                         = aws_cognito_user_pool.employees.id
  name                                 = "alb"
  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = [aws_cognito_identity_provider.google.provider_name]
  callback_urls                        = [for host in values(local.apps) : "https://${host}/oauth2/idpresponse"]
}
