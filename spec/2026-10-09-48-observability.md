---
status: draft
issue: 48
intent: intent/2026-10-09-48-observability.md
---

# Spec: Dashboards, autoscaling and log encryption

## Design

The approved intent picked these answers:

- **Q1 b):** a golden-signals dashboard with the five alarms on it and no Logs
  Insights widgets.
- **Q2 a):** web target tracking on CPU at 60%, min 2, max 4. The worker stays at 1.
- **Q3 b):** a new `aws_kms_key.logs` behind a variable that defaults to on.
  `aws_kms_key.data` (`infra/storage.tf:1-4`) is not touched.

The intent did not ask for new alarms, so this spec adds none. That includes
the optional p95 latency alarm.

### 1. Variables (`infra/variables.tf`)

| Variable | Type | Default | Notes |
| --- | --- | --- | --- |
| `web_min_count` | number | `2` | Replaces `web_desired_count` (`variables.tf:74-77`). It is the scaling floor and the service's initial `desired_count`. |
| `web_max_count` | number | `4` | `validation`: `>= var.web_min_count`. |
| `web_cpu_target` | number | `60` | Average service CPU %. `validation`: between 10 and 90. |
| `log_kms_encryption` | bool | `true` | Encrypts the four app log groups with the new log key. |

`web_desired_count` is removed rather than kept unused. A tfvars file that
still sets it gets the "undeclared variable" warning, and the README says to
rename it. `terraform.tfvars.example` does not set it today.

### 2. Log key (`infra/logs.tf`)

```hcl
locals {
  log_groups = ["web", "worker", "crawl", "migrate"] # also the for_each below
}

resource "aws_kms_key" "logs" {
  count               = var.log_kms_encryption ? 1 : 0
  description         = "${var.name} CloudWatch Logs: app log groups"
  enable_key_rotation = true
  policy              = data.aws_iam_policy_document.logs_key.json
}

resource "aws_kms_alias" "logs" {
  count         = var.log_kms_encryption ? 1 : 0
  name          = "alias/${var.name}-logs"
  target_key_id = aws_kms_key.logs[0].key_id
}

resource "aws_cloudwatch_log_group" "app" {
  for_each          = toset(local.log_groups)
  name              = "/ecs/${var.name}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.log_kms_encryption ? aws_kms_key.logs[0].arn : null
}
```

The key policy is a `data "aws_iam_policy_document" "logs_key"`. The account
id comes from `data.aws_caller_identity.current` and the region from
`var.region`, so no id appears in code. It has three statements, each checked
against the AWS guide "Encrypt log data in CloudWatch Logs using AWS KMS"
(docs.aws.amazon.com/AmazonCloudWatch/latest/logs/encrypt-log-data-kms.html):

1. **`EnableIAM`.** Principal: the AWS account root of this account. Action
   `kms:*`, resource `*`. This is the default key-policy statement. It keeps the
   key manageable through IAM and must never be dropped (see Risks).
2. **`CloudWatchLogs`.**
   - Principal: the service `logs.${var.region}.amazonaws.com`. The guide says
     it must be the key's own region.
   - Actions, exactly as the guide lists them: `kms:Encrypt`, `kms:Decrypt`,
     `kms:ReEncrypt*`, `kms:GenerateDataKey*`, `kms:Describe*`. The intent's
     `Encrypt*` and `Decrypt*` are tightened to the documented names.
   - Resource: `*`.
   - Condition: `ArnEquals` on `kms:EncryptionContext:aws:logs:arn`. The values
     are the four log-group ARNs, built as strings:
     `arn:aws:logs:${var.region}:${account_id}:log-group:/ecs/${var.name}/${g}`
     for `g` in `local.log_groups`. They are not taken from
     `aws_cloudwatch_log_group.app[*].arn`, because the log group references
     the key and that would make a cycle.
3. **`ExecRolesViaLogs`.**
   - Principal: the four `aws_iam_role.exec[*].arn`.
   - Actions: `kms:Encrypt`, `kms:GenerateDataKey*`, `kms:Describe*`.
   - Resource: `*`.
   - Condition: `StringEquals` `kms:ViaService = logs.${var.region}.amazonaws.com`.

   The guide now says that a principal that calls `PutLogEvents` on a
   CMK-encrypted group needs KMS permission through `kms:ViaService`. The
   awslogs driver calls `PutLogEvents` as the exec role (`infra/iam.tf:36-39`).
   The grant lives in the key policy, so `iam.tf` does not change. There is no
   cycle: the key depends on the roles, and the role *policies* depend on the
   log groups.

Operators who read the logs (console, Logs Insights) need `kms:Decrypt` on
this key, granted through IAM. Statement 1 delegates that to IAM, and admin
roles with `kms:*` already have it.

Ordering: the log group references the key ARN, so Terraform creates the key
and its policy before it associates the key with the group.

### 3. Web autoscaling (`infra/ecs.tf`)

- `aws_ecs_service.web` (`ecs.tf:155-181`):
  - `desired_count = var.web_min_count`.
  - Add `lifecycle { ignore_changes = [desired_count] }`, so applies do not
    reset the scaler's count.
- The worker service (`ecs.tf:183-200`) is unchanged.
- New resources, after the web service:

```hcl
resource "aws_appautoscaling_target" "web" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.web.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.web_min_count
  max_capacity       = var.web_max_count
}

resource "aws_appautoscaling_policy" "web_cpu" {
  name               = "${var.name}-web-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.web.service_namespace
  resource_id        = aws_appautoscaling_target.web.resource_id
  scalable_dimension = aws_appautoscaling_target.web.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = var.web_cpu_target
    scale_out_cooldown = 60  # slow requests pile up fast
    scale_in_cooldown  = 300 # do not flap
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
```

Application Auto Scaling uses its service-linked role
(`AWSServiceRoleForApplicationAutoScaling_ECSService`). AWS creates that role
on first use, so no IAM resource is added. Target tracking creates and owns its
own two CloudWatch alarms. They are not wired to SNS.

### 4. Dashboard (`infra/monitoring.tf`, appended)

A single `aws_cloudwatch_dashboard.main`, with `dashboard_name = var.name` and
`dashboard_body = jsonencode({ widgets = [...] })`. All widgets are `metric`
type unless noted. Every widget sets `region = var.region`. Periods are 60 s
(300 s for RDS storage).

| Row | Widget | Metrics |
| --- | --- | --- |
| 1 | Alarm status (`type = "alarm"`) | `alarms = [the 5 alarm .arn]` from this file. |
| 2 | ALB requests | `AWS/ApplicationELB` `RequestCount` Sum, dimension `LoadBalancer = aws_lb.main.arn_suffix`. |
| 2 | ALB latency | `TargetResponseTime`, stats p50, p95, p99, same dimension. |
| 2 | ALB errors | `HTTPCode_Target_4XX_Count`, `HTTPCode_Target_5XX_Count` and `HTTPCode_ELB_5XX_Count`, all Sum. |
| 2 | Web hosts | `HealthyHostCount` and `UnHealthyHostCount`, Maximum, with dimensions `LoadBalancer` and `TargetGroup = aws_lb_target_group.web.arn_suffix`. |
| 3 | ECS CPU % | `AWS/ECS` `CPUUtilization` Average for `ServiceName` web and worker (`ClusterName = aws_ecs_cluster.main.name`). A horizontal annotation at `var.web_cpu_target`. |
| 3 | ECS memory % | `AWS/ECS` `MemoryUtilization` Average, web and worker. |
| 3 | ECS tasks | `ECS/ContainerInsights` `RunningTaskCount` and `DesiredTaskCount`, web and worker. Horizontal annotations at the min and max web counts. |
| 4 | RDS CPU | `AWS/RDS` `CPUUtilization`, `DBInstanceIdentifier = aws_db_instance.main.identifier`. |
| 4 | RDS connections | `DatabaseConnections`. |
| 4 | RDS free storage | `FreeStorageSpace` Minimum, with an annotation at the alarm threshold. |
| 4 | RDS latency | `ReadLatency` and `WriteLatency`. |

The layout uses explicit `x`, `y`, `width` and `height` (24-column grid, rows
of 6 units). There are no Logs Insights (`log`) widgets.

### 5. Outputs (`infra/outputs.tf`)

```hcl
output "dashboard_url" {
  description = "CloudWatch dashboard for the service."
  value       = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/${aws_cloudwatch_dashboard.main.dashboard_name}"
}
```

No key ARN output. The alias `alias/<name>-logs` is enough to find the key.

### 6. README (`infra/README.md:63-64`)

The "Alarms" bullet gains three things:

- the dashboard name and the `dashboard_url` output;
- web scaling: 2 to 4 tasks on CPU 60%, worker fixed at 1 (#45);
- log encryption: the `<name>-logs` key, the `log_kms_encryption` switch, and
  the rename of `web_desired_count` to `web_min_count`.

## Alternatives rejected

- **Give `aws_kms_key.data` a policy (Q3 a).** A mistake there locks out S3,
  RDS and the task roles, and there is no staging to catch it (#25).
- **Default encryption only (Q3 c).** The logs hold user names and error text,
  while every other data store uses a customer key.
- **Scale on `ALBRequestCountPerTarget`, or on both metrics.** It needs a
  guessed requests-per-task number, and web load is CPU-bound PDF and research
  work.
- **Scale the worker.** Blocked until #45 adds a shared per-domain limit.
- **Logs Insights widgets.** They are billed per GB scanned every time the
  dashboard refreshes. They can be added later.
- **A wildcard `ArnLike` (`/ecs/<name>/*`) in the key policy.** It is simpler,
  but it would let any future log group under the prefix use the key. Four
  exact ARNs are just as short with `for`.
- **Keep `web_desired_count` next to min/max.** That leaves three numbers that
  must agree. The scaler owns the count now.
- **Grant the exec roles KMS in `iam.tf`.** This works too, but it would split
  the key's access across two files. The key policy keeps it next to the key.

## Risks

- **Cost.**
  - Dashboard: about $3/month (beyond the free three per account).
  - KMS key: $1/month, plus about $0.03 per 10k requests. CloudWatch Logs
    caches data keys, so request volume stays low.
  - Scaling can add up to 2 more web Fargate tasks (1 vCPU, 2 GB each) under
    load. That cost is bounded by `web_max_count`.
  - Target tracking adds 2 alarms (about $0.20/month).
- **Key-policy lock-out.**
  - A key policy without the `EnableIAM` statement makes the key unmanageable.
    Only AWS Support can recover it.
  - Mitigations:
    - The statement is first and written literally.
    - The new key guards logs only, so the data key is untouched.
    - Review checks the rendered policy (`terraform console` on the data
      source) before merge.
  - If the `CloudWatchLogs` statement is wrong, the association fails. Logs
    are refused while the key is associated, and old data cannot be read.
- **`kms_key_id` on existing log groups.**
  - The provider calls `AssociateKmsKey` in place. The group is not replaced
    and its history is kept.
  - Data written before the change stays under default encryption. Only new
    events use the key.
  - The guide says association takes up to 5 minutes. Events in that window
    use whichever key is active.
  - If the key policy does not yet allow Logs, the apply fails. Terraform's
    dependency order (key and policy first) prevents this.
- **Turning the switch off later.**
  - `log_kms_encryption = false` disassociates the key and then schedules the
    key for deletion (30-day window).
  - After deletion, events written under the key can no longer be read. The
    README says to wait out `log_retention_days`, or to keep the key, before
    switching off.
- **The exec-role statement may be unnecessary.** If AWS still accepts
  `PutLogEvents` without it, it is a harmless extra grant, scoped by
  `kms:ViaService`. If AWS needs it and it is missing, tasks fail to start
  (the awslogs driver errors).
- **The `desired_count` change on the first apply.** `ignore_changes` stops
  later drift. The first apply keeps 2 tasks, because `web_min_count`
  defaults to 2.
- **Untested against AWS.** There is no `plan` or `apply` until #25 (staging).
  This spec is checked by validate only.

## Verification

In `infra/`:

1. `terraform fmt -check -recursive` exits 0.
2. `terraform init -backend=false && terraform validate` reports
   "Success! The configuration is valid."
3. `tflint` passes if it is installed. It is not on this machine today, so the
   plan records that it was skipped.
4. `grep -nE '[0-9]{12}|arn:aws:[a-z]+:[a-z0-9-]+:[0-9]' infra/*.tf` finds no
   account id or literal ARN.
5. By reading the code, confirm:
   - `EnableIAM` is present;
   - the condition key is `kms:EncryptionContext:aws:logs:arn` with the four
     group ARNs;
   - `ignore_changes = [desired_count]` is on web only;
   - the worker is unchanged;
   - the dashboard has no `log` widgets.

No `terraform plan` or `apply`.
