# Alarms -> SNS. The topic is not KMS-encrypted: CloudWatch cannot publish to a topic encrypted with the
# AWS-managed key. Subscribe a pager or mailing list (alarm_email), then confirm the subscription.
resource "aws_sns_topic" "alarms" {
  name = "${var.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  alarm_actions = [aws_sns_topic.alarms.arn]
}

# ALB-generated plus application 5xx responses, summed
resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${var.name}-alb-5xx"
  alarm_description   = "More than 5 server errors in 5 minutes"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 5
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  metric_query {
    id          = "total"
    expression  = "FILL(elb, 0) + FILL(target, 0)" # each series exists only when non-zero
    label       = "5xx"
    return_data = true
  }

  metric_query {
    id = "elb"

    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_ELB_5XX_Count"
      period      = 300
      stat        = "Sum"
      dimensions  = { LoadBalancer = aws_lb.main.arn_suffix }
    }
  }

  metric_query {
    id = "target"

    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      period      = 300
      stat        = "Sum"
      dimensions  = { LoadBalancer = aws_lb.main.arn_suffix }
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "web_unhealthy" {
  alarm_name          = "${var.name}-web-unhealthy-hosts"
  alarm_description   = "A web task failed its health check"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  dimensions          = { LoadBalancer = aws_lb.main.arn_suffix, TargetGroup = aws_lb_target_group.web.arn_suffix }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "worker_down" {
  alarm_name          = "${var.name}-worker-not-running"
  alarm_description   = "No worker task is running: nothing is being crawled, extracted or researched"
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  dimensions          = { ClusterName = aws_ecs_cluster.main.name, ServiceName = aws_ecs_service.worker.name }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 5
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching" # no data at all means no task is reporting
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${var.name}-rds-cpu"
  alarm_description   = "Database CPU above 80% for 15 minutes"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# 10% of the configured size; storage autoscaling grows the disk, so this errs on the early side
resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  alarm_name          = "${var.name}-rds-free-storage"
  alarm_description   = "Less than 10% of the configured database storage is free"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = var.db_allocated_storage * 1024 * 1024 * 1024 * 0.1
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

locals {
  dash_alb     = aws_lb.main.arn_suffix
  dash_tg      = aws_lb_target_group.web.arn_suffix
  dash_cluster = aws_ecs_cluster.main.name
  dash_db      = aws_db_instance.main.identifier
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = var.name
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "alarm", x = 0, y = 0, width = 24, height = 6
        properties = {
          title = "Alarms"
          alarms = [
            aws_cloudwatch_metric_alarm.alb_5xx.arn,
            aws_cloudwatch_metric_alarm.web_unhealthy.arn,
            aws_cloudwatch_metric_alarm.worker_down.arn,
            aws_cloudwatch_metric_alarm.rds_cpu.arn,
            aws_cloudwatch_metric_alarm.rds_storage.arn,
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 6, height = 6
        properties = {
          title  = "ALB requests"
          region = var.region
          period = 60
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", local.dash_alb, { stat = "Sum" }],
          ]
        }
      },
      {
        type = "metric", x = 6, y = 6, width = 6, height = 6
        properties = {
          title  = "ALB latency"
          region = var.region
          period = 60
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", local.dash_alb, { stat = "p50" }],
            ["...", { stat = "p95" }],
            ["...", { stat = "p99" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 6, height = 6
        properties = {
          title  = "ALB errors"
          region = var.region
          period = 60
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_Target_4XX_Count", "LoadBalancer", local.dash_alb, { stat = "Sum" }],
            [".", "HTTPCode_Target_5XX_Count", ".", ".", { stat = "Sum" }],
            [".", "HTTPCode_ELB_5XX_Count", ".", ".", { stat = "Sum" }],
          ]
        }
      },
      {
        type = "metric", x = 18, y = 6, width = 6, height = 6
        properties = {
          title  = "Web hosts"
          region = var.region
          period = 60
          metrics = [
            ["AWS/ApplicationELB", "HealthyHostCount", "LoadBalancer", local.dash_alb, "TargetGroup", local.dash_tg, { stat = "Maximum" }],
            [".", "UnHealthyHostCount", ".", ".", ".", ".", { stat = "Maximum" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 8, height = 6
        properties = {
          title  = "ECS CPU %"
          region = var.region
          period = 60
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", local.dash_cluster, "ServiceName", aws_ecs_service.web.name, { stat = "Average" }],
            ["...", aws_ecs_service.worker.name, { stat = "Average" }],
          ]
          annotations = { horizontal = [{ label = "web CPU target", value = var.web_cpu_target }] }
        }
      },
      {
        type = "metric", x = 8, y = 12, width = 8, height = 6
        properties = {
          title  = "ECS memory %"
          region = var.region
          period = 60
          metrics = [
            ["AWS/ECS", "MemoryUtilization", "ClusterName", local.dash_cluster, "ServiceName", aws_ecs_service.web.name, { stat = "Average" }],
            ["...", aws_ecs_service.worker.name, { stat = "Average" }],
          ]
        }
      },
      {
        type = "metric", x = 16, y = 12, width = 8, height = 6
        properties = {
          title  = "ECS tasks"
          region = var.region
          period = 60
          metrics = [
            ["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", local.dash_cluster, "ServiceName", aws_ecs_service.web.name, { stat = "Average" }],
            [".", "DesiredTaskCount", ".", ".", ".", ".", { stat = "Average" }],
            [".", "RunningTaskCount", ".", ".", ".", aws_ecs_service.worker.name, { stat = "Average" }],
            [".", "DesiredTaskCount", ".", ".", ".", ".", { stat = "Average" }],
          ]
          annotations = {
            horizontal = [
              { label = "web min", value = var.web_min_count },
              { label = "web max", value = var.web_max_count },
            ]
          }
        }
      },
      {
        type = "metric", x = 0, y = 18, width = 6, height = 6
        properties = {
          title  = "RDS CPU"
          region = var.region
          period = 60
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", local.dash_db, { stat = "Average" }],
          ]
        }
      },
      {
        type = "metric", x = 6, y = 18, width = 6, height = 6
        properties = {
          title  = "RDS connections"
          region = var.region
          period = 60
          metrics = [
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", local.dash_db, { stat = "Average" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 18, width = 6, height = 6
        properties = {
          title  = "RDS free storage"
          region = var.region
          period = 300
          metrics = [
            ["AWS/RDS", "FreeStorageSpace", "DBInstanceIdentifier", local.dash_db, { stat = "Minimum" }],
          ]
          annotations = { horizontal = [{ label = "alarm", value = aws_cloudwatch_metric_alarm.rds_storage.threshold }] }
        }
      },
      {
        type = "metric", x = 18, y = 18, width = 6, height = 6
        properties = {
          title  = "RDS latency"
          region = var.region
          period = 60
          metrics = [
            ["AWS/RDS", "ReadLatency", "DBInstanceIdentifier", local.dash_db, { stat = "Average" }],
            [".", "WriteLatency", ".", ".", { stat = "Average" }],
          ]
        }
      },
    ]
  })
}
