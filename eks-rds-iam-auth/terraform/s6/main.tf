terraform {
  backend "local" {}
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.72.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type = string
}

variable "name" {
  type    = string
  default = "eks-rds-iam-auth"
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "cluster_security_group_id" {
  type = string
}

variable "rds_security_group_id" {
  type = string
}

variable "db_instance_id" {
  type = string
}

variable "db_resource_id" {
  type = string
}

variable "db_user" {
  type    = string
  default = "app_iam"
}

data "aws_caller_identity" "this" {}

data "aws_partition" "this" {}

resource "aws_iam_role" "proxy" {
  name = "syw-eks-rds-proxy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "proxy" {
  name = "syw-rds-db-connect"
  role = aws_iam_role.proxy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "rds-db:connect"
      Resource = "arn:${data.aws_partition.this.partition}:rds-db:${var.region}:${data.aws_caller_identity.this.account_id}:dbuser:${var.db_resource_id}/${var.db_user}"
    }]
  })
}

resource "aws_security_group" "proxy" {
  name   = "${var.name}-proxy"
  vpc_id = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "proxy_from_nodes" {
  security_group_id            = aws_security_group.proxy.id
  referenced_security_group_id = var.cluster_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}

resource "aws_vpc_security_group_egress_rule" "proxy_to_rds" {
  security_group_id            = aws_security_group.proxy.id
  referenced_security_group_id = var.rds_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_proxy" {
  security_group_id            = var.rds_security_group_id
  referenced_security_group_id = aws_security_group.proxy.id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}

resource "aws_db_proxy" "this" {
  name                   = var.name
  engine_family          = "MYSQL"
  default_auth_scheme    = "IAM_AUTH"
  require_tls            = true
  role_arn               = aws_iam_role.proxy.arn
  vpc_subnet_ids         = var.subnet_ids
  vpc_security_group_ids = [aws_security_group.proxy.id]
  depends_on             = [aws_iam_role_policy.proxy]
}

resource "aws_db_proxy_default_target_group" "this" {
  db_proxy_name = aws_db_proxy.this.name
}

resource "aws_db_proxy_target" "this" {
  db_proxy_name          = aws_db_proxy.this.name
  target_group_name      = aws_db_proxy_default_target_group.this.name
  db_instance_identifier = var.db_instance_id
}

output "proxy_endpoint" {
  value = aws_db_proxy.this.endpoint
}

output "proxy_resource_id" {
  value = element(split(":", aws_db_proxy.this.arn), 6)
}
