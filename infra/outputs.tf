output "app_url" {
  value = var.app_origin
}

output "alb_dns_name" {
  description = "Point the app_origin DNS name (private zone) at this."
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "bucket_name" {
  value = aws_s3_bucket.originals.bucket
}

output "rds_endpoint" {
  value = aws_db_instance.main.endpoint
}

output "migrate_task" {
  description = "Run once after every deploy that changes the schema: aws ecs run-task (see README)."
  value = {
    cluster         = aws_ecs_cluster.main.name
    task_definition = aws_ecs_task_definition.app["migrate"].family
    subnets         = aws_subnet.private[*].id
    security_group  = aws_security_group.crawl.id
  }
}

output "db_master_secret_arn" {
  description = "RDS-managed master credentials (read by the tasks; do not copy)."
  value       = local.db_secret
}

output "secret_arns_to_fill" {
  description = "Set a value for each of these before the first deploy."
  value       = local.secret_arn
}

output "upload_source_config" {
  description = "Config for the 'upload' source on /admin/sources."
  value       = jsonencode({ bucket = aws_s3_bucket.originals.bucket, prefix = var.upload_prefix })
}

output "share_source_configs" {
  description = "Config for one 's3' source per DataSync share on /admin/sources."
  value       = { for k, s in local.shares : k => jsonencode({ bucket = aws_s3_bucket.originals.bucket, prefix = "shares/${k}/" }) }
}
