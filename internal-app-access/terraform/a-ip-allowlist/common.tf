terraform {
  required_version = "1.17.1"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.73.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "4.4.2"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "2.8.1"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

variable "app_domain_suffix" {
  type    = string
  default = "internal-app-access.example.com"
}

locals {
  name   = "employee-apps-a"
  domain = var.app_domain_suffix
  apps = {
    wiki    = "wiki.${local.domain}"
    expense = "expense.${local.domain}"
    admin   = "admin.${local.domain}"
  }
}

module "base" {
  source = "../modules/base"
  name   = local.name
  domain = local.domain
}

output "alb_name" {
  value = local.name
}
