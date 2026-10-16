# Created empty: set values out of band (README). The six connection secrets may stay empty.
locals {
  secret_names = toset(["session-secret", "oidc-client-secret", "graph-client-secret", "confluence-token", "brave-api-key", "ollama-cloud-key", "openai-api-key", "gemini-api-key", "db-app-password"])
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
  conn_secret_ids = {
    GRAPH_CLIENT_SECRET = local.secret_arn["graph-client-secret"]
    CONFLUENCE_TOKEN    = local.secret_arn["confluence-token"]
    BRAVE_API_KEY       = local.secret_arn["brave-api-key"]
    OLLAMA_CLOUD_KEY    = local.secret_arn["ollama-cloud-key"]
    OPENAI_API_KEY      = local.secret_arn["openai-api-key"]
    GEMINI_API_KEY      = local.secret_arn["gemini-api-key"]
  }
}
