resource "aws_iam_policy" "app" {
  name = "app"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        NotAction = ["iam:*", "organizations:*", "account:*"]
        Resource  = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "app" {
  role       = aws_iam_role.app.name
  policy_arn = aws_iam_policy.app.arn
}
