---
status: approved
issue: 48
spec: spec/2026-10-09-48-observability.md
---

# Plan: Dashboards, autoscaling and log encryption

## Approved decisions (self-contained)

From the approved intent (Q1 b, Q2 a, Q3 b) and spec:

- **Scope:** one golden-signals dashboard (no Logs Insights widgets), web CPU
  target tracking (min 2, max 4, 60%), a new log KMS key. **No new alarms**
  (not even p95 latency). `aws_kms_key.data` (`infra/storage.tf:1-4`), the
  worker service, `iam.tf` and `terraform.tfvars.example` are not touched.
- **Variables** (`infra/variables.tf`):

  | Variable | Type | Default | Validation / notes |
  | --- | --- | --- | --- |
  | `web_min_count` | number | `2` | Replaces `web_desired_count`. Scaling floor and the web service's initial `desired_count`. |
  | `web_max_count` | number | `4` | `>= 1` (the max ≥ min check is a precondition, below) |
  | `web_cpu_target` | number | `60` | `var.web_cpu_target >= 10 && var.web_cpu_target <= 90` |
  | `log_kms_encryption` | bool | `true` | Encrypts the four app log groups with the new key. |

  `web_desired_count` is **removed**, not kept. The max ≥ min check is a
  `lifecycle { precondition { condition = var.web_max_count >=
  var.web_min_count … } }` on `aws_appautoscaling_target.web`, which works
  on the existing `required_version = ">= 1.6"`. So there's no version bump:
  a cross-variable `validation` block would need 1.9 (session model's
  amendment, keeping the plan within the spec).
- **Log key** (`infra/logs.tf`): `local.log_groups = ["web", "worker",
  "crawl", "migrate"]` drives the existing `for_each` (same keys, so
  `iam.tf:38` and `ecs.tf` references keep working);
  `aws_kms_key.logs` and `aws_kms_alias.logs` (`alias/${var.name}-logs`), both
  `count = var.log_kms_encryption ? 1 : 0`, `enable_key_rotation = true`;
  `aws_cloudwatch_log_group.app` gains
  `kms_key_id = var.log_kms_encryption ? aws_kms_key.logs[0].arn : null`.
- **Key policy** `data "aws_iam_policy_document" "logs_key"`, three statements;
  account id from `data.aws_caller_identity.current.account_id`
  (`versions.tf:23`), region from `var.region`. No literal ids.
  1. `sid = "EnableIAM"`: principal AWS `arn:aws:iam::${account_id}:root`,
     `kms:*`, resource `*`. **Must be first and must never be removed.**
  2. `sid = "CloudWatchLogs"`: principal Service `logs.${var.region}.amazonaws.com`;
     actions `kms:Encrypt`, `kms:Decrypt`, `kms:ReEncrypt*`,
     `kms:GenerateDataKey*`, `kms:Describe*`; resource `*`; condition
     `ArnEquals` `kms:EncryptionContext:aws:logs:arn` = the four strings
     `arn:aws:logs:${var.region}:${account_id}:log-group:/ecs/${var.name}/${g}`
     for `g` in `local.log_groups` (strings, **not**
     `aws_cloudwatch_log_group.app[*].arn`: that is a cycle).
  3. `sid = "ExecRolesViaLogs"`: principal AWS `[for r in aws_iam_role.exec : r.arn]`;
     actions `kms:Encrypt`, `kms:GenerateDataKey*`, `kms:Describe*`;
     resource `*`; condition `StringEquals` `kms:ViaService` =
     `logs.${var.region}.amazonaws.com`.

  **Statement 3 verified, kept.** The AWS guide "Encrypt log data in
  CloudWatch Logs using AWS KMS"
  (https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/encrypt-log-data-kms.html,
  read 2026-10-09), Step 3 "Permissions for reading and writing encrypted log
  data": a principal calling `PutLogEvents` on a CMK-encrypted group "needs
  additional AWS KMS permissions", granted in the key policy or IAM with
  `kms:ViaService = logs.<region>.amazonaws.com`. The awslogs driver calls
  `PutLogEvents` as the exec role (`iam.tf:36-39`). So this is not a
  deviation from the spec. The guide's example also lists `kms:Decrypt` and
  `kms:ReEncrypt*` for read+write; the exec roles only write, so the spec's
  write-only subset stands (Risk below).
  No cycle: key policy → exec roles; log groups → key; role *policies* → log groups.
- **Web autoscaling** (`infra/ecs.tf`): web `desired_count = var.web_min_count`
  plus `lifecycle { ignore_changes = [desired_count] }`;
  `aws_appautoscaling_target.web` and `aws_appautoscaling_policy.web_cpu`
  exactly as in step 3. Service-linked role is created by AWS; no IAM added.
- **Dashboard** `aws_cloudwatch_dashboard.main` in `infra/monitoring.tf`
  (layout in step 4). **Output** `dashboard_url`. No key ARN output.
- **README** (`infra/README.md:63-64`): dashboard, scaling, log key, and the
  `web_desired_count` → `web_min_count` rename.
- **Coder handoff:** 6 file-editing steps on 6 files, so steps 1–6 go to one
  `coder` agent, step by step via `SendMessage`. Step 7 is the session model's.

## Common checks and traps

**→ verify by** (after every step, from the worktree root):

```sh
terraform fmt -check -recursive infra
terraform -chdir=infra init -backend=false && terraform -chdir=infra validate
```

Expected: `fmt` exits 0; validate prints "Success! The configuration is valid."

**Traps (all steps):**

- **No `terraform plan` or `apply`.** There is no staging until #25.
- **Public repo:** no account ids, ARNs with ids, emails or real hostnames in
  code, README or commit messages. Build ARNs from
  `data.aws_caller_identity.current` and `var.region`.
- Commit each step on `feat/48-observability`, citing "plan #48 step N".

## Steps

1. **`infra/variables.tf:74-77`, `infra/ecs.tf:159`: rename and new variables.**
   - Replace the `web_desired_count` block (lines 74-77) with `web_min_count`,
     `web_max_count`, `web_cpu_target` (types, defaults, validations from the
     table, each with a one-line `description`; error messages say the rule).
   - Add `log_kms_encryption` (bool, `true`) after `log_retention_days`
     (lines 102-105), description "Encrypt the app log groups with the
     `<name>-logs` KMS key. Before turning it off, read the README."
   - `ecs.tf:159`: `desired_count = var.web_min_count` (keep alignment; fmt).
   - `versions.tf` is unchanged (`>= 1.6`): no cross-variable `validation`
     blocks; the max ≥ min check is the precondition on the scaling target.

   → verify by the common checks, plus `grep -rn web_desired_count infra` → no hits.
   Traps: the rename and `ecs.tf:159` must land in the same commit or validate
   fails. `terraform.tfvars.example` does not set `web_desired_count`; leave it.
   An operator tfvars that still sets it gets an "undeclared variable"
   warning; step 6 documents the rename.

2. **`infra/logs.tf` (whole file, 5 lines today): log key, alias, policy, association.**
   - Add `locals { log_groups = [...] }` and use `toset(local.log_groups)` in the
     existing `for_each`; add `kms_key_id` as in the decisions.
   - Add `data "aws_iam_policy_document" "logs_key"` with the three statements
     in the order and wording above (use `sid`, `principals`, `condition { test,
     variable, values }`), then `aws_kms_key.logs` (description
     `"${var.name} CloudWatch Logs: app log groups"`, `policy =
     data.aws_iam_policy_document.logs_key.json`) and `aws_kms_alias.logs`.

   → verify by the common checks, plus:
   `grep -nE '[0-9]{12}|arn:aws:[a-z]+:[a-z0-9-]+:[0-9]' infra/*.tf` → no hits.
   Traps: do not derive ARNs from `aws_cloudwatch_log_group.app` in the policy
   (cycle). Do not edit `iam.tf` or `aws_kms_key.data`. `EnableIAM` first,
   literal. The deployer running `AssociateKmsKey` needs `kms:DescribeKey`;
   `EnableIAM` delegates that to its IAM admin rights, so nothing else to add.

3. **`infra/ecs.tf:155-181` (web service) and after line 181: autoscaling.**
   - In `aws_ecs_service.web` add `lifecycle { ignore_changes = [desired_count] }`
     (after `depends_on`).
   - After the web service, add:

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

   → verify by the common checks, plus `git diff infra/ecs.tf` shows no change
   in `aws_ecs_service.worker` (lines 183-200 before the edit).
   Traps: `ignore_changes` on web only. No IAM role for scaling. The target
   tracking alarms are AWS-owned; do not add SNS actions to them.

4. **`infra/monitoring.tf` (append after line 124): dashboard.**
   `aws_cloudwatch_dashboard.main`, `dashboard_name = var.name`,
   `dashboard_body = jsonencode({ widgets = [...] })`. Every widget sets
   `region = var.region`; `period = 60` except RDS free storage (`300`).
   24-column grid, height 6 per row, explicit `x`, `y`, `width`, `height`.
   Use `local.alb = aws_lb.main.arn_suffix`, `tg = aws_lb_target_group.web.arn_suffix`,
   `cluster = aws_ecs_cluster.main.name`, `db = aws_db_instance.main.identifier`
   (a `locals` block is fine).

   | Row (y) | Widget (width) | Metrics |
   | --- | --- | --- |
   | 0 | Alarm status, `type = "alarm"` (24) | `alarms = [alb_5xx, web_unhealthy, worker_down, rds_cpu, rds_storage]` `.arn` |
   | 6 | ALB requests (6) | `AWS/ApplicationELB RequestCount` Sum, `LoadBalancer` |
   | 6 | ALB latency (6) | `TargetResponseTime` p50, p95, p99, `LoadBalancer` |
   | 6 | ALB errors (6) | `HTTPCode_Target_4XX_Count`, `HTTPCode_Target_5XX_Count`, `HTTPCode_ELB_5XX_Count` Sum |
   | 6 | Web hosts (6) | `HealthyHostCount`, `UnHealthyHostCount` Maximum, `LoadBalancer` + `TargetGroup` |
   | 12 | ECS CPU % (8) | `AWS/ECS CPUUtilization` Average, `ClusterName` + `ServiceName` web, worker; horizontal annotation at `var.web_cpu_target` |
   | 12 | ECS memory % (8) | `AWS/ECS MemoryUtilization` Average, web, worker |
   | 12 | ECS tasks (8) | `ECS/ContainerInsights RunningTaskCount`, `DesiredTaskCount`, web, worker; annotations at `var.web_min_count`, `var.web_max_count` |
   | 18 | RDS CPU (6) | `AWS/RDS CPUUtilization`, `DBInstanceIdentifier` |
   | 18 | RDS connections (6) | `DatabaseConnections` |
   | 18 | RDS free storage (6) | `FreeStorageSpace` Minimum, period 300; annotation at `aws_cloudwatch_metric_alarm.rds_storage.threshold` |
   | 18 | RDS latency (6) | `ReadLatency`, `WriteLatency` |

   → verify by the common checks, plus `grep -n '"log"' infra/monitoring.tf` → no hits.
   Traps: no `type = "log"` widgets. No new `aws_cloudwatch_metric_alarm`.
   Container Insights is already on (`ecs.tf:114-117`); do not touch it.
   `metrics` arrays use the `[ns, name, dimKey, dimVal, ..., { stat = ... }]` form.

5. **`infra/outputs.tf` (append after line 50): `dashboard_url`.**

   ```hcl
   output "dashboard_url" {
     description = "CloudWatch dashboard for the service."
     value       = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/${aws_cloudwatch_dashboard.main.dashboard_name}"
   }
   ```

   → verify by the common checks.
   Traps: no key ARN output.

6. **`infra/README.md:63-64` ("Alarms" bullet): docs.**
   Extend the bullet (or add sub-bullets) with:
   - the dashboard `<name>` and the `dashboard_url` output;
   - web scaling: 2 to 4 tasks on average CPU 60% (`web_min_count`,
     `web_max_count`, `web_cpu_target`); worker fixed at 1 (#45);
   - log encryption: key `alias/<name>-logs`, switch `log_kms_encryption`;
     operators reading logs need `kms:Decrypt` on it via IAM; before
     switching it off, keep the key or wait out `log_retention_days` (a
     disabled or deleted key makes those events unreadable);
   - **rename:** a tfvars that sets `web_desired_count` must rename it to
     `web_min_count`.

   → verify by the common checks (unchanged .tf) and a read of the rendered bullet.
   Traps: no account ids or real hostnames in examples. Do not edit
   `terraform.tfvars.example` (it does not set the old variable).

7. **Session model: final checks and the PR.** Run Tests below; render the key
   policy (`terraform -chdir=infra console` needs state/credentials, so
   instead read the HCL against the decisions list); open the PR linking
   intent, spec and plan, saying steps 1–6 were done by `coder`.


*Review (fresh Opus): no blockers.* Taken:
- *Deviation:* `aws_kms_key.logs` and its alias no longer depend on
  `log_kms_encryption`. With `count = 0`, switching off would have scheduled
  the key for deletion (30 days) while logs are kept 90, so events would
  become unreadable. The key always exists (about $1/month), the switch only
  sets `kms_key_id`, and `deletion_window_in_days = 30` is explicit.
- `web_min_count` is validated `>= 1`.
- The README text now matches the code.

Not taken: widening the exec roles to AWS's read-and-write example. They
write only; check delivery on the first apply (#25) and add `Decrypt` and
`ReEncrypt*` only if needed.

## Tests

From the worktree root:

1. `terraform fmt -check -recursive infra` → exit 0.
2. `terraform -chdir=infra init -backend=false && terraform -chdir=infra validate`
   → "Success! The configuration is valid."
3. `tflint` → not installed here; record "skipped" in the PR.
4. `grep -nE '[0-9]{12}|arn:aws:[a-z]+:[a-z0-9-]+:[0-9]' infra/*.tf` → no hits.
5. `grep -rn web_desired_count infra` → no hits outside the README rename note.
6. Read the code and confirm: `EnableIAM` present and first; condition key
   `kms:EncryptionContext:aws:logs:arn` with four group ARNs; `ExecRolesViaLogs`
   scoped by `kms:ViaService`; `ignore_changes = [desired_count]` on web only;
   worker unchanged; no `log` widgets; no new alarms.

No `terraform plan` or `apply` (#25).

## Risks carried from the spec

- Key-policy lock-out if `EnableIAM` is dropped (only AWS Support recovers).
- Exec roles get the write-only subset (`Encrypt`, `GenerateDataKey*`,
  `Describe*`). If tasks fail to start with an awslogs KMS error after the
  first apply, add `kms:Decrypt` and `kms:ReEncrypt*` to statement 3 (the
  guide's full set) and update this plan in the same commit.
- Association takes up to 5 minutes; earlier data stays under default
  encryption.
- Untested against AWS until #25.

## Rollback

- Before merge: close the PR; the branch holds only these commits.
- After merge, before apply: `git revert` the merge commit.
- After apply: set `log_kms_encryption = false` only after keeping the key or
  waiting out `log_retention_days` (it schedules key deletion, 30-day window;
  `aws kms cancel-key-deletion` undoes it in that window). Reverting the
  autoscaling resources returns web to a fixed `desired_count`; re-add
  `web_desired_count` in the revert if operators' tfvars still use it.
  Deleting the dashboard has no side effects.
