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

variable "cluster_name" {
  type = string
}

variable "connect_resource_id" {
  type = string
}

variable "db_user" {
  type    = string
  default = "app_iam"
}

data "aws_caller_identity" "this" {}

data "aws_partition" "this" {}

resource "aws_iam_role" "app" {
  name = "syw-eks-rds-app"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy" "app" {
  name = "syw-rds-db-connect"
  role = aws_iam_role.app.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "rds-db:connect"
      Resource = "arn:${data.aws_partition.this.partition}:rds-db:${var.region}:${data.aws_caller_identity.this.account_id}:dbuser:${var.connect_resource_id}/${var.db_user}"
    }]
  })
}

resource "aws_eks_pod_identity_association" "app" {
  cluster_name    = var.cluster_name
  namespace       = "app"
  service_account = "app"
  role_arn        = aws_iam_role.app.arn
}

output "policy" {
  value = aws_iam_role_policy.app.policy
}
