# Created empty: set the values out of band (README). A task cannot start while a secret it uses has no value.
locals {
  use_openai = startswith(var.extract_model, "openai/") || startswith(var.draft_model, "openai/")
  use_gemini = startswith(var.extract_model, "gemini/") || startswith(var.draft_model, "gemini/")
  secret_names = toset(concat(
    ["session-secret", "oidc-client-secret", "graph-client-secret", "confluence-token", "brave-api-key", "db-app-password"],
    local.use_openai ? ["openai-api-key"] : [],
    local.use_gemini ? ["gemini-api-key"] : [],
  ))
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
  provider_secrets = merge(
    local.use_openai ? { OPENAI_API_KEY = local.secret_arn["openai-api-key"] } : {},
    local.use_gemini ? { GEMINI_API_KEY = local.secret_arn["gemini-api-key"] } : {},
  )
}
