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

resource "aws_iam_role" "tokencheck" {
  name = "syw-eks-rds-tokencheck"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_eks_pod_identity_association" "tokencheck" {
  cluster_name    = var.cluster_name
  namespace       = "tokencheck"
  service_account = "tokencheck"
  role_arn        = aws_iam_role.tokencheck.arn
}
