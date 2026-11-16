terraform {
  required_version = "1.17.1"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.71.0"
    }
  }
}

provider "aws" {}

variable "boundary_arn" {
  type = string
}

variable "trusted_principal" {
  type = string
}

resource "aws_iam_role" "worker" {
  name                 = "terraform-worker"
  path                 = "/app/"
  permissions_boundary = var.boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = var.trusted_principal }
      Action    = "sts:AssumeRole"
    }]
  })
}
