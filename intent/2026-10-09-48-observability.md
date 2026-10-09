---
status: approved
issue: 48
author: olafkfreund
---

# Intent: Dashboards, autoscaling and log encryption

## Problem

The deployment in `infra/` has alarms but no overview, a fixed number of web
tasks, and app logs that use only the default encryption.

- **No dashboard.** `infra/monitoring.tf` has an SNS topic (lines 3-5) and five
  alarms: ALB 5xx (19-59), unhealthy web hosts (61-75), no running worker
  (77-91), RDS CPU (93-107) and RDS free storage (110-124). When one fires,
  nothing shows request rate, latency, task CPU or memory, or queue health next
  to it. Each metric has to be found in the console by hand. Container Insights
  is already on (`infra/ecs.tf:114-117`), so the task metrics exist but nothing
  reads them. The reference-engine plan left "alarms and dashboards" open
  (`plan/2026-10-06-1-reference-engine.md:788-789`), and `infra/README.md:63-64`
  lists only the alarms.
- **The web service cannot scale.** `aws_ecs_service.web` sets a fixed
  `desired_count = var.web_desired_count` (`infra/ecs.tf:155-181`; default 2 in
  `infra/variables.tf:74-77`). There is no `aws_appautoscaling_*` resource
  anywhere in `infra/`. A burst of slow requests (PDF conversion and research
  pages, `infra/alb.tf:8`) is served by two tasks or not at all. Two tasks also
  run all night with nothing to do.
- **Log groups use the default encryption.** `infra/logs.tf:1-5` creates
  `/ecs/<name>/{web,worker,crawl,migrate}` with no `kms_key_id`. Everything
  else that holds data uses the customer key `aws_kms_key.data`
  (`infra/storage.tf:1-4`): the originals bucket (`storage.tf:50-51`) and RDS
  (`storage.tf:99`). The logs hold request paths, user names and error text.
  That key has no `policy` argument, so it gets the default key policy, which
  does not let the CloudWatch Logs service use it.

## Proposed outcome

- `terraform apply` creates one CloudWatch dashboard, `<name>`, showing ALB
  traffic, errors and latency, web and worker CPU, memory and task counts, and
  RDS health, with the existing alarms on it. The README says where to find it.
- The web service scales between a minimum and a maximum task count on one
  metric. The current behaviour (2 tasks) stays the default floor unless the
  approver picks otherwise. The worker is not scaled (see Constraints).
- A variable turns on KMS encryption for the four app log groups. The key
  policy lets CloudWatch Logs in this region use the key, scoped to this
  stack's log groups.
- `terraform fmt -check` and `terraform validate` pass.

## Affected users and systems

- `infra/monitoring.tf`: new dashboard (or a new `dashboard.tf`).
- `infra/ecs.tf:155-181`: the web service gains scaling. `desired_count` must
  stop fighting the scaler, e.g. `lifecycle { ignore_changes = [desired_count] }`.
- `infra/logs.tf:1-5`: `kms_key_id` on the log groups.
- `infra/storage.tf:1-4`: a key policy on `aws_kms_key.data`, or a new key.
- `infra/variables.tf`: min/max web tasks, scaling target, a log-encryption
  switch.
- `infra/README.md:63-64` ("Alarms"), plus a line on scaling and log
  encryption.
- Operators who read the dashboard, and whoever pays the AWS bill.
- No app code. No change to the worker, crawl or migrate services beyond their
  log groups.

## Constraints

- **Terraform only.** Validate with `terraform fmt -check` and
  `terraform validate`. No `terraform plan` or `apply`: there is no staging yet
  (#25, "Run terraform plan and apply in staging", open).
- **Cost.** A dashboard costs about $3/month beyond the free three. A log
  KMS key adds request charges on every log batch. Reusing the existing key
  adds no monthly key fee; a new key adds $1/month. Scaling must not raise the
  floor above today's 2 web tasks without the approver saying so, and the max
  must be bounded.
- **KMS key policy for CloudWatch Logs.** The Logs service principal
  `logs.<region>.amazonaws.com` needs `kms:Encrypt*`, `kms:Decrypt*`,
  `kms:ReEncrypt*`, `kms:GenerateDataKey*` and `kms:Describe*`, with a
  `kms:EncryptionContext:aws:logs:arn` condition limited to this stack's log
  groups. Adding a policy to `aws_kms_key.data` replaces the default one, so it
  must keep the account-root statement. Without it the key becomes
  unmanageable, and S3, RDS and the roles in `infra/iam.tf:97-98,116-117` and
  `infra/datasync.tf:39-40` keep working only through that statement.
  Associating a key with an existing log group works in place, but the key must
  allow Logs first, or the apply fails.
- **Worker stays at 1.** `infra/variables.tf:90-94`: research jobs rate-limit
  per job only, so no worker scaling until #45 adds a shared limit.
- **No secrets.** The repo is public: no account ids, ARNs, emails or keys in
  code, examples or this file. Build ARNs from `data.aws_caller_identity` and
  `var.region`, as `infra/storage.tf:12` already does.
- The SNS topic stays unencrypted (`infra/monitoring.tf:1-2`); out of scope.

## Open questions

1. **What the dashboard shows.**
   - a) Only the alarmed metrics plus their alarm widgets.
   - b) The golden signals: ALB request count, target response time p50/p95/p99,
     4xx/5xx, healthy/unhealthy hosts; ECS CPU, memory and running tasks for
     web and worker (Container Insights); RDS CPU, connections, free storage,
     read/write latency; an alarm-status widget for the five alarms.
   - c) b) plus Logs Insights widgets (error lines per service).
   - **Lean: b).** Every metric already exists, and none costs extra beyond the
     dashboard. c) runs queries that are billed per scan; add it later if
     needed. No new alarms in this issue except, optionally, ALB p95 latency.
2. **What drives web autoscaling, and min/max.**
   - a) Target tracking on `ECSServiceAverageCPUUtilization` (e.g. 60%).
   - b) Target tracking on `ALBRequestCountPerTarget`.
   - c) Both.
   - **Lean: a) CPU at 60%, min 2, max 4.** Web work is CPU-bound and slow per
     request (PDF and research), so request count is a poor proxy for load,
     and a request-count target needs a guessed number of requests per task.
     Min 2 keeps one task in each of the two AZs (`infra/network.tf:28-31`), as
     today. Max 4 bounds cost and RDS
     connections. Scheduled scale-in at night is out of scope.
3. **Which key for the log groups.**
   - a) Reuse `aws_kms_key.data` and give it an explicit policy.
   - b) A new `aws_kms_key.logs` with its own policy.
   - c) Leave logs on the default encryption, and only document it.
   - **Lean: b), behind a variable that defaults to on.** A separate key keeps
     the new policy off the key that guards the data bucket and RDS, so a
     mistake cannot lock those out, and access to logs can be granted apart
     from access to data. The cost is $1/month. a) is cheaper but rewrites the
     policy of the most important key with no staging to test on (#25).
