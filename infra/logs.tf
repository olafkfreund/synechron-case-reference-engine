locals {
  log_groups = ["web", "worker", "crawl", "migrate"]
}

# Statement order matters: EnableIAM must stay first and must never be removed,
# or the key is locked out (only AWS Support can recover it).
data "aws_iam_policy_document" "logs_key" {
  statement {
    sid       = "EnableIAM"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  statement {
    sid       = "CloudWatchLogs"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
    # Built from strings, not aws_cloudwatch_log_group.app[*].arn: that would be a cycle.
    condition {
      test     = "ArnEquals"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = [for g in local.log_groups : "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${var.name}/${g}"]
    }
  }

  statement {
    sid       = "ExecRolesViaLogs"
    actions   = ["kms:Encrypt", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [for r in aws_iam_role.exec : r.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["logs.${var.region}.amazonaws.com"]
    }
  }
}

# the key always exists: switching encryption off must not delete it, or logs written under it become unreadable
resource "aws_kms_key" "logs" {
  description             = "${var.name} CloudWatch Logs: app log groups"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.logs_key.json
}

resource "aws_kms_alias" "logs" {
  name          = "alias/${var.name}-logs"
  target_key_id = aws_kms_key.logs.key_id
}

resource "aws_cloudwatch_log_group" "app" {
  for_each          = toset(local.log_groups)
  name              = "/ecs/${var.name}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.log_kms_encryption ? aws_kms_key.logs.arn : null
}
