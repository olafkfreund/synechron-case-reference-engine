# File shares -> s3://<bucket>/shares/<name>/ (the S3 crawler reads that prefix). Nothing is created
# when var.datasync_shares is empty. The DataSync agent is activated out of band (README).
locals {
  shares = { for s in var.datasync_shares : s.name => s }
}

data "aws_iam_policy_document" "datasync_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["datasync.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

data "aws_iam_policy_document" "datasync" {
  statement {
    actions   = ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"]
    resources = [aws_s3_bucket.originals.arn]
  }

  statement {
    actions = [
      "s3:AbortMultipartUpload", "s3:DeleteObject", "s3:GetObject", "s3:ListMultipartUploadParts",
      "s3:PutObject", "s3:GetObjectTagging", "s3:PutObjectTagging",
    ]
    resources = ["${aws_s3_bucket.originals.arn}/shares/*"]
  }

  statement {
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [aws_kms_key.data.arn]
  }
}

resource "aws_iam_role" "datasync" {
  count              = length(local.shares) > 0 ? 1 : 0
  name               = "${var.name}-datasync"
  assume_role_policy = data.aws_iam_policy_document.datasync_assume.json
}

resource "aws_iam_role_policy" "datasync" {
  count  = length(local.shares) > 0 ? 1 : 0
  name   = "shares"
  role   = aws_iam_role.datasync[0].id
  policy = data.aws_iam_policy_document.datasync.json
}

data "aws_secretsmanager_secret_version" "share" {
  for_each  = local.shares
  secret_id = each.value.password_secret_arn
}

resource "aws_datasync_location_smb" "share" {
  for_each        = local.shares
  server_hostname = each.value.smb_server
  subdirectory    = each.value.path
  domain          = each.value.subdomain == "" ? null : each.value.subdomain
  user            = each.value.user
  password        = data.aws_secretsmanager_secret_version.share[each.key].secret_string
  agent_arns      = [each.value.agent_arn]

  mount_options {
    version = "AUTOMATIC"
  }
}

resource "aws_datasync_location_s3" "share" {
  for_each      = local.shares
  s3_bucket_arn = aws_s3_bucket.originals.arn
  subdirectory  = "/shares/${each.key}/"

  s3_config {
    bucket_access_role_arn = aws_iam_role.datasync[0].arn
  }

  depends_on = [aws_iam_role_policy.datasync]
}

resource "aws_datasync_task" "share" {
  for_each                 = local.shares
  name                     = "${var.name}-${each.key}"
  source_location_arn      = aws_datasync_location_smb.share[each.key].arn
  destination_location_arn = aws_datasync_location_s3.share[each.key].arn

  schedule {
    schedule_expression = each.value.schedule
  }

  options {
    verify_mode            = "ONLY_FILES_TRANSFERRED"
    transfer_mode          = "CHANGED"
    preserve_deleted_files = "REMOVE" # a file deleted on the share must disappear from search
    posix_permissions      = "NONE"
    uid                    = "NONE"
    gid                    = "NONE"
  }
}
