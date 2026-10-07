resource "aws_cloudwatch_log_group" "app" {
  for_each          = toset(["web", "worker", "crawl", "migrate"])
  name              = "/ecs/${var.name}/${each.key}"
  retention_in_days = var.log_retention_days
}
