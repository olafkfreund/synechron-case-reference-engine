# Created empty: set the values out of band (README). A task cannot start while a secret it uses has no value.
locals {
  secret_names = toset(["session-secret", "oidc-client-secret", "graph-client-secret", "confluence-token", "brave-api-key", "db-app-password"])
}

resource "aws_secretsmanager_secret" "app" {
  for_each                = local.secret_names
  name                    = "${var.name}/${each.key}"
  recovery_window_in_days = var.secret_recovery_days
}

# db-app-password: JSON {"password": "..."}, the password of the restricted database role refs_app.
# The migrate task sets the role's password from it; the app tasks read it at runtime.
locals {
  secret_arn = { for k, s in aws_secretsmanager_secret.app : k => s.arn }
  db_secret  = aws_db_instance.main.master_user_secret[0].secret_arn
}
