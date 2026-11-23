terraform {
  backend "local" {}
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.72.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.7.2"
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

variable "kubernetes_version" {
  type    = string
  default = "1.36"
}

variable "mysql_version" {
  type    = string
  default = "8.4.11"
}

data "aws_availability_zones" "this" {
  state = "available"
}

data "aws_partition" "this" {}

locals {
  azs = slice(data.aws_availability_zones.this.names, 0, 2)
}

resource "aws_vpc" "this" {
  cidr_block           = "10.40.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = var.name }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(aws_vpc.this.cidr_block, 4, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.name}-${local.azs[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = { Name = var.name }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_iam_role" "cluster" {
  name = "syw-eks-rds-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:${data.aws_partition.this.partition}:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_eks_cluster" "this" {
  name     = var.name
  version  = var.kubernetes_version
  role_arn = aws_iam_role.cluster.arn

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  vpc_config {
    subnet_ids              = aws_subnet.public[*].id
    endpoint_public_access  = true
    endpoint_private_access = true
  }

  upgrade_policy {
    support_type = "STANDARD"
  }

  depends_on = [aws_iam_role_policy_attachment.cluster]
}

resource "aws_iam_role" "node" {
  name = "syw-eks-rds-node"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each   = toset(["AmazonEKSWorkerNodePolicy", "AmazonEKS_CNI_Policy", "AmazonEC2ContainerRegistryReadOnly"])
  role       = aws_iam_role.node.name
  policy_arn = "arn:${data.aws_partition.this.partition}:iam::aws:policy/${each.value}"
}

resource "aws_eks_node_group" "this" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = var.name
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = aws_subnet.public[*].id
  ami_type        = "AL2023_ARM_64_STANDARD"
  instance_types  = ["t4g.medium"]
  capacity_type   = "ON_DEMAND"

  scaling_config {
    desired_size = 1
    min_size     = 1
    max_size     = 1
  }

  depends_on = [aws_iam_role_policy_attachment.node]
}

resource "aws_eks_addon" "pod_identity_agent" {
  cluster_name = aws_eks_cluster.this.name
  addon_name   = "eks-pod-identity-agent"
  depends_on   = [aws_eks_node_group.this]
}

resource "aws_ecr_repository" "image" {
  for_each             = toset(["before", "after", "loadgen", "tokencheck"])
  name                 = "${var.name}/${each.value}"
  force_delete         = true
  image_tag_mutability = "MUTABLE"
}

resource "aws_security_group" "rds" {
  name   = "${var.name}-rds"
  vpc_id = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_nodes" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_rotation" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = aws_security_group.rotation.id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}

resource "aws_security_group" "rotation" {
  name   = "${var.name}-rotation"
  vpc_id = aws_vpc.this.id
}

resource "aws_vpc_security_group_egress_rule" "rotation" {
  security_group_id = aws_security_group.rotation.id
  cidr_ipv4         = aws_vpc.this.cidr_block
  ip_protocol       = "-1"
}

resource "aws_security_group" "endpoint" {
  name   = "${var.name}-endpoint"
  vpc_id = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "endpoint" {
  security_group_id = aws_security_group.endpoint.id
  cidr_ipv4         = aws_vpc.this.cidr_block
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.public[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true
}

resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = aws_subnet.public[*].id
}

resource "random_password" "admin" {
  length  = 32
  special = false
}

resource "aws_db_instance" "this" {
  identifier                          = var.name
  engine                              = "mysql"
  engine_version                      = var.mysql_version
  instance_class                      = "db.t4g.small"
  allocated_storage                   = 20
  storage_type                        = "gp3"
  db_name                             = "appdb"
  username                            = "admin"
  password                            = random_password.admin.result
  db_subnet_group_name                = aws_db_subnet_group.this.name
  vpc_security_group_ids              = [aws_security_group.rds.id]
  publicly_accessible                 = false
  iam_database_authentication_enabled = false
  backup_retention_period             = 0
  skip_final_snapshot                 = true
  apply_immediately                   = true

  lifecycle {
    ignore_changes = [iam_database_authentication_enabled]
  }
}

output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "cluster_security_group_id" {
  value = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
}

output "vpc_id" {
  value = aws_vpc.this.id
}

output "subnet_ids" {
  value = aws_subnet.public[*].id
}

output "rds_security_group_id" {
  value = aws_security_group.rds.id
}

output "rotation_security_group_id" {
  value = aws_security_group.rotation.id
}

output "db_instance_id" {
  value = aws_db_instance.this.identifier
}

output "db_address" {
  value = aws_db_instance.this.address
}

output "db_resource_id" {
  value = aws_db_instance.this.resource_id
}

output "db_admin_password" {
  value     = random_password.admin.result
  sensitive = true
}

output "registry" {
  value = split("/", aws_ecr_repository.image["before"].repository_url)[0]
}
