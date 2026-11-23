terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
    archive = {
      source = "hashicorp/archive"
    }
  }
}

variable "name" {
  type = string
}

variable "internal" {
  type = bool
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "certificate_arn" {
  type = string
}

variable "apps" {
  type = map(string)
}

variable "default_app" {
  type    = string
  default = null
}

variable "cognito" {
  type = object({
    user_pool_arn       = string
    user_pool_client_id = string
    user_pool_domain    = string
  })
  default = null
}

locals {
  priorities = { for i, k in sort(keys(var.apps)) : k => 10 + i }
}

resource "aws_security_group" "alb" {
  name   = "${var.name}-alb"
  vpc_id = var.vpc_id
}

resource "aws_lb" "this" {
  name               = var.name
  internal           = var.internal
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.subnet_ids
}

data "archive_file" "app" {
  type        = "zip"
  source_file = "${path.module}/app/app.py"
  output_path = "${path.root}/.terraform-build/app.zip"
}

resource "aws_iam_role" "app" {
  name = "syw-${var.name}-app"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/aws/lambda/${var.name}-app"
  retention_in_days = 90
}

resource "aws_iam_role_policy" "app_logs" {
  role = aws_iam_role.app.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.app.arn}:*"
    }]
  })
}

resource "aws_lambda_function" "app" {
  function_name    = "${var.name}-app"
  role             = aws_iam_role.app.arn
  runtime          = "python3.13"
  handler          = "app.handler"
  filename         = data.archive_file.app.output_path
  source_code_hash = data.archive_file.app.output_base64sha256

  depends_on = [aws_cloudwatch_log_group.app, aws_iam_role_policy.app_logs]
}

resource "aws_lb_target_group" "app" {
  for_each    = var.apps
  name        = "${var.name}-${each.key}-fn"
  target_type = "lambda"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lambda_permission" "alb" {
  for_each      = var.apps
  statement_id  = "alb-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app.function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.app[each.key].arn
}

resource "aws_lb_target_group_attachment" "app" {
  for_each         = var.apps
  target_group_arn = aws_lb_target_group.app[each.key].arn
  target_id        = aws_lambda_function.app.arn

  depends_on = [aws_lambda_permission.alb]
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  dynamic "default_action" {
    for_each = var.default_app != null && var.cognito != null ? [var.cognito] : []

    content {
      type  = "authenticate-cognito"
      order = 1

      authenticate_cognito {
        user_pool_arn       = default_action.value.user_pool_arn
        user_pool_client_id = default_action.value.user_pool_client_id
        user_pool_domain    = default_action.value.user_pool_domain
      }
    }
  }

  dynamic "default_action" {
    for_each = var.default_app != null ? [var.default_app] : []

    content {
      type             = "forward"
      order            = 2
      target_group_arn = aws_lb_target_group.app[default_action.value].arn
    }
  }

  dynamic "default_action" {
    for_each = var.default_app == null ? [1] : []

    content {
      type = "fixed-response"

      fixed_response {
        content_type = "text/plain"
        status_code  = "404"
      }
    }
  }
}

resource "aws_lb_listener_rule" "app" {
  for_each     = var.apps
  listener_arn = aws_lb_listener.https.arn
  priority     = local.priorities[each.key]

  dynamic "action" {
    for_each = var.cognito == null ? [] : [var.cognito]

    content {
      type  = "authenticate-cognito"
      order = 1

      authenticate_cognito {
        user_pool_arn       = action.value.user_pool_arn
        user_pool_client_id = action.value.user_pool_client_id
        user_pool_domain    = action.value.user_pool_domain
      }
    }
  }

  action {
    type             = "forward"
    order            = 2
    target_group_arn = aws_lb_target_group.app[each.key].arn
  }

  condition {
    host_header {
      values = [each.value]
    }
  }
}

output "security_group_id" {
  value = aws_security_group.alb.id
}

output "arn" {
  value = aws_lb.this.arn
}

output "dns_name" {
  value = aws_lb.this.dns_name
}

output "zone_id" {
  value = aws_lb.this.zone_id
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.app.name
}
