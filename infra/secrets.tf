# Created empty: set the values out of band (README). A task cannot start while a secret it uses has no value.
locals {
  secret_names = toset(["session-secret", "oidc-client-secret", "graph-client-secret", "confluence-token", "brave-api-key"])
}

resource "aws_secretsmanager_secret" "app" {
  for_each                = local.secret_names
  name                    = "${var.name}/${each.key}"
  recovery_window_in_days = var.secret_recovery_days
}

locals {
  secret_arn = { for k, s in aws_secretsmanager_secret.app : k => s.arn }
  db_secret  = aws_db_instance.main.master_user_secret[0].secret_arn
}
