module "alb" {
  source          = "../modules/alb"
  name            = local.name
  internal        = false
  vpc_id          = module.base.vpc_id
  subnet_ids      = module.base.public_subnet_ids
  certificate_arn = module.base.certificate_arn
  apps            = local.apps
  default_app     = "wiki"

  cognito = {
    user_pool_arn       = aws_cognito_user_pool.employees.arn
    user_pool_client_id = aws_cognito_user_pool_client.alb.id
    user_pool_domain    = aws_cognito_user_pool_domain.this.domain
  }
}

variable "cognito_domain_prefix" {
  type = string
}

variable "saml_metadata_file" {
  type    = string
  default = ""
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

  schema {
    name                = "groups"
    attribute_data_type = "String"
    mutable             = true

    string_attribute_constraints {
      min_length = 0
      max_length = 2048
    }
  }
}

resource "aws_cognito_identity_provider" "google" {
  user_pool_id  = aws_cognito_user_pool.employees.id
  provider_name = "GoogleWorkspace"
  provider_type = "SAML"

  provider_details = {
    MetadataFile = var.saml_metadata_file != "" ? file(var.saml_metadata_file) : templatefile("${path.module}/google-saml-metadata.xml.tftpl", {
      certificate = join("", slice(split("\n", trimspace(module.base.certificate_pem)), 1, length(split("\n", trimspace(module.base.certificate_pem))) - 1))
    })
  }

  attribute_mapping = {
    email           = "email"
    "custom:groups" = "groups"
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
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = [aws_cognito_identity_provider.google.provider_name]
  callback_urls                        = concat(["https://${module.alb.dns_name}/oauth2/idpresponse"], [for host in values(local.apps) : "https://${host}/oauth2/idpresponse"])
}

output "alb_dns_name" {
  value = module.alb.dns_name
}

output "cognito_domain" {
  value = "${aws_cognito_user_pool_domain.this.domain}.auth.us-east-1.amazoncognito.com"
}

output "user_pool_id" {
  value = aws_cognito_user_pool.employees.id
}

output "log_group_name" {
  value = module.alb.log_group_name
}
