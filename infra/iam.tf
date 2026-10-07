data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ---- task execution roles: pull the image, write logs, read ONLY that task's secrets -----------

locals {
  exec_secrets = {
    web    = [local.secret_arn["session-secret"], local.secret_arn["oidc-client-secret"]]
    worker = [local.secret_arn["graph-client-secret"], local.secret_arn["confluence-token"], local.secret_arn["brave-api-key"]]
    crawl  = []
  }
}

data "aws_iam_policy_document" "exec" {
  for_each = local.exec_secrets

  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = [aws_ecr_repository.app.arn]
  }

  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.app[each.key].arn}:*"]
  }

  dynamic "statement" {
    for_each = length(each.value) > 0 ? [1] : []
    content {
      actions   = ["secretsmanager:GetSecretValue"]
      resources = each.value
    }
  }
}

resource "aws_iam_role" "exec" {
  for_each           = local.exec_secrets
  name               = "${var.name}-${each.key}-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "exec" {
  for_each = local.exec_secrets
  name     = "exec"
  role     = aws_iam_role.exec[each.key].id
  policy   = data.aws_iam_policy_document.exec[each.key].json
}

# ---- task roles --------------------------------------------------------------------------------

data "aws_iam_policy_document" "bedrock" {
  statement {
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = var.bedrock_model_arns
  }
}

# the RDS-managed master secret: read at runtime by the app (it rotates), so by the TASK roles
data "aws_iam_policy_document" "db_secret" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.db_secret]
  }
}

data "aws_iam_policy_document" "web" {
  source_policy_documents = [data.aws_iam_policy_document.bedrock.json, data.aws_iam_policy_document.db_secret.json]

  statement { # uploads only: the web task never reads or overwrites originals
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.originals.arn}/${var.upload_prefix}*"]
  }

  statement {
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [aws_kms_key.data.arn]
  }
}

data "aws_iam_policy_document" "worker" {
  source_policy_documents = [data.aws_iam_policy_document.bedrock.json, data.aws_iam_policy_document.db_secret.json]

  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.originals.arn]
  }

  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.originals.arn}/*"]
  }

  statement {
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [aws_kms_key.data.arn]
  }
}

resource "aws_iam_role" "web" {
  name               = "${var.name}-web"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "web" {
  name   = "app"
  role   = aws_iam_role.web.id
  policy = data.aws_iam_policy_document.web.json
}

resource "aws_iam_role" "worker" {
  name               = "${var.name}-worker"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "worker" {
  name   = "app"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

resource "aws_iam_role" "crawl" {
  name               = "${var.name}-crawl"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "crawl" { # enqueues jobs: the database only
  name   = "app"
  role   = aws_iam_role.crawl.id
  policy = data.aws_iam_policy_document.db_secret.json
}
