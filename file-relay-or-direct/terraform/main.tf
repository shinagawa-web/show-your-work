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

variable "client_cidr" {
  type = string
}

variable "app_binary" {
  type = string
}

variable "cf_public_key_pem" {
  type = string
}

variable "cf_private_key_pem" {
  type      = string
  sensitive = true
}

variable "instance_type" {
  type = string
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-kernel-*-x86_64"]
  }
}

data "aws_ec2_instance_type_offerings" "app" {
  location_type = "availability-zone"
  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  tags                 = { Name = "file-relay-or-direct" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = sort(data.aws_ec2_instance_type_offerings.app.locations)[0]
  map_public_ip_on_launch = true
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]
}

resource "aws_security_group" "app" {
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 8080
    to_port     = 8081
    protocol    = "tcp"
    cidr_blocks = [var.client_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_s3_bucket" "files" {
  bucket_prefix = "file-relay-or-direct-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "files" {
  bucket                  = aws_s3_bucket.files.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "app_binary" {
  bucket = aws_s3_bucket.files.id
  key    = "bin/relay-bench"
  source = var.app_binary
  etag   = filemd5(var.app_binary)
}

resource "aws_cloudfront_origin_access_control" "files" {
  name                              = "file-relay-or-direct"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_public_key" "app" {
  name        = "file-relay-or-direct"
  encoded_key = var.cf_public_key_pem
}

resource "aws_cloudfront_key_group" "app" {
  name  = "file-relay-or-direct"
  items = [aws_cloudfront_public_key.app.id]
}

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_cloudfront_distribution" "files" {
  enabled     = true
  price_class = "PriceClass_200"

  origin {
    origin_id                = "s3"
    domain_name              = aws_s3_bucket.files.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.files.id
  }

  default_cache_behavior {
    target_origin_id       = "s3"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
    trusted_key_groups     = [aws_cloudfront_key_group.app.id]
  }

  ordered_cache_behavior {
    path_pattern           = "public/*"
    target_origin_id       = "s3"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
  }

  ordered_cache_behavior {
    path_pattern           = "private/*"
    target_origin_id       = "s3"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
    trusted_key_groups     = [aws_cloudfront_key_group.app.id]
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

resource "aws_s3_bucket_policy" "files" {
  bucket     = aws_s3_bucket.files.id
  depends_on = [aws_s3_bucket_public_access_block.files]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = ["${aws_s3_bucket.files.arn}/public/*", "${aws_s3_bucket.files.arn}/private/*"]
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.files.arn } }
    }]
  })
}

resource "aws_iam_role" "app" {
  name = "syw-file-relay-app"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "app" {
  name = "read-write-files"
  role = aws_iam_role.app.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
      Resource = "${aws_s3_bucket.files.arn}/*"
    }]
  })
}

resource "aws_iam_instance_profile" "app" {
  name = "syw-file-relay-app"
  role = aws_iam_role.app.name
}

resource "aws_iam_role" "signer" {
  name = "syw-file-relay-signer"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
      Action    = "sts:AssumeRole"
      Condition = {
        ArnLike = {
          "aws:PrincipalArn" = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/*${split("/", data.aws_caller_identity.current.arn)[1]}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "signer" {
  name = "read-private-files"
  role = aws_iam_role.signer.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:GetObject"
      Resource = "${aws_s3_bucket.files.arn}/private/*"
    }]
  })
}

resource "aws_instance" "app" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.app.id]
  iam_instance_profile        = aws_iam_instance_profile.app.name
  user_data_replace_on_change = true
  depends_on                  = [aws_vpc_endpoint.s3, aws_iam_role_policy.app, aws_route_table_association.public]

  metadata_options {
    http_tokens = "required"
  }

  user_data = templatefile("${path.module}/user-data.sh", {
    bucket          = aws_s3_bucket.files.id
    region          = data.aws_region.current.region
    binary_etag     = aws_s3_object.app_binary.etag
    cf_domain       = aws_cloudfront_distribution.files.domain_name
    cf_key_id       = aws_cloudfront_public_key.app.id
    cf_private_key  = var.cf_private_key_pem
  })
}

output "bucket" {
  value = aws_s3_bucket.files.id
}

output "app_ip" {
  value = aws_instance.app.public_ip
}

output "ami" {
  value = aws_instance.app.ami
}

output "availability_zone" {
  value = aws_instance.app.availability_zone
}

output "cf_domain" {
  value = aws_cloudfront_distribution.files.domain_name
}

output "cf_distribution_id" {
  value = aws_cloudfront_distribution.files.id
}

output "signer_role_arn" {
  value = aws_iam_role.signer.arn
}
