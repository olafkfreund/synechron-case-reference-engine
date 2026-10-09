# Deploying the reference engine

One root module: VPC, ECS Fargate (web + worker), internal ALB, RDS PostgreSQL 16, S3 + KMS, Secrets Manager,
daily crawl schedule, optional DataSync. Nothing is applied by CI; run it from a trusted machine.

## Before you start (owners in the plan)

| Need | Owner |
| ---- | ----- |
| AWS account and region; **Bedrock model access enabled** for the two models; their ARNs | Cloud team |
| Entra app registration (OIDC): redirect URI `<app_origin>/auth`, and **"Groups assigned to the application"** so tokens never overflow; the group ids for the three roles | IT / identity |
| An ACM certificate for the app's host name; a private DNS record for it pointing at the ALB | Cloud team |
| SharePoint: Graph app with **`Sites.Selected` granted per site with the `fullcontrol` role** (a read-only grant cannot see permissions, so every file would be skipped) | M365 admin |
| Confluence service account that can read the spaces and their restrictions | Confluence admin |
| Brave Search API key (zero-data-retention plan) | Procurement |
| File shares: a **DataSync agent** deployed on a host that can reach them, and **activated** in the target region (console or `aws datasync create-agent`); Terraform takes its ARN | Infra |

## Deploy order

1. **State backend** (once): an S3 bucket (versioned, encrypted, private) and a DynamoDB lock table. The state holds
   DataSync share passwords if you use shares, so restrict it.
2. `cd infra && terraform init -backend-config="bucket=..." -backend-config="key=reference-engine/prod.tfstate" -backend-config="region=..." -backend-config="dynamodb_table=..."`
3. Copy `terraform.tfvars.example` to `prod.tfvars` and fill it in.
4. `terraform apply -target=aws_ecr_repository.app -var-file=prod.tfvars`, then **build and push the image**
   (`docker build`, tag it with `image_tag`, push to the `ecr_repository_url` output; tags are immutable).
5. `terraform apply -var-file=prod.tfvars` creates everything else. The services will not become healthy yet:
6. **Set the secret values** (outputs `secret_arns_to_fill`). A task cannot start while a secret it uses is empty:
   ```
   aws secretsmanager put-secret-value --secret-id reference-engine/session-secret --secret-string "$(openssl rand -base64 48)"
   aws secretsmanager put-secret-value --secret-id reference-engine/oidc-client-secret --secret-string '...'
   aws secretsmanager put-secret-value --secret-id reference-engine/db-app-password \
     --secret-string "{\"password\": \"$(openssl rand -base64 36 | tr -d '/+=')\"}"
   # graph-client-secret, confluence-token, brave-api-key likewise (put a placeholder if unused)
   ```
7. **Run the migrate task** (creates the tables and the restricted database role `refs_app`, with the password from
   `db-app-password`). Wait for it to stop and check its exit code; do this again after any release that changes `sql/`:
   ```
   eval "$(terraform output -json migrate_task | jq -r '"aws ecs run-task --cluster \(.cluster) --launch-type FARGATE --task-definition \(.task_definition) --network-configuration awsvpcConfiguration={subnets=[\(.subnets|join(","))],securityGroups=[\(.security_group)]}"')"
   ```
8. Start the services: `aws ecs update-service --cluster reference-engine --service web --force-new-deployment` (and `worker`).
9. DNS: point the app host name at `alb_dns_name` (private zone). Confirm the alarm email subscription (`alarm_email`).
10. In the app, as an admin, open `/admin/sources` and add the sources. Use the `upload_source_config` and
   `share_source_configs` outputs for the upload and file-share sources, and set each source's access groups.

## Things to know

- **The web task has HTTPS egress through the NAT** (decided 2026-10-07): the OIDC login calls Entra ID server-side
  (metadata, token, keys), which has no small stable address range. The web task never parses untrusted documents;
  the worker does. Bedrock, Secrets Manager, ECR, logs and STS still go through VPC endpoints, S3 through the gateway.
- **Bedrock in EU regions uses cross-region inference profiles** (`eu.anthropic....`): put the profile id in
  `extract_model`/`draft_model`, and both the profile ARN and the foundation-model ARNs (any EU region) in
  `bedrock_model_arns`; see `terraform.tfvars.example`.
- **Bedrock needs no `EXTRACT_MODEL_OPTIONS`/`DRAFT_MODEL_OPTIONS`** (mode `schema`, destination `our-cloud`) until #26 decides otherwise.
- The worker has HTTPS egress through the NAT (research, Graph, Confluence). DNS64/NAT64 is off; set no proxy variables.
- RDS creates the master password in Secrets Manager and rotates it every 7 days. The app reads it at runtime
  (`DB_SECRET_ARN`, task role) and fetches it again after a rotation; it is never injected at task start. TLS to RDS
  is `verify-full` against the RDS CA bundle baked into the image.
- **The app does not use the master user.** web, worker and the crawl task connect as `refs_app` (rows only: no DDL, no
  superuser), with the password from `db-app-password`. Only the `migrate` task has the master secret; the app
  processes do not create tables at start (`DB_INIT_ON_START` exists for local compose only).
  To rotate `db-app-password`: put the new value in the secret first, then run the migrate task (the app keeps the
  old password until it is refused, then fetches the new one).
- **Alarms** go to the SNS topic `<name>-alarms`: ALB 5xx, unhealthy web hosts, no running worker, RDS CPU and low RDS
  storage. **ALB access logs** go to a separate S3 bucket (SSE-S3, 90 days).
- **Dashboard** `<name>` in CloudWatch (golden signals: alarms, ALB, ECS, RDS); the URL is the `dashboard_url` output.
- **Web scaling:** 2 to 4 tasks on average CPU 60% (`web_min_count`, `web_max_count`, `web_cpu_target`); the worker
  stays at 1 (#45). **Rename:** a tfvars that sets `web_desired_count` must now set `web_min_count`.
- **Log encryption:** the app log groups use the KMS key `alias/<name>-logs` (switch: `log_kms_encryption`). Operators
  reading the logs need `kms:Decrypt` on it via IAM. The key always exists: switching encryption off only stops
  the log groups using it, so events written while it was on stay readable. Never delete the key while those
  events are within `log_retention_days`.
- The daily crawl is an EventBridge Scheduler task running `python -m app.enqueue_crawls`; the worker crawls.
  Run it by hand from `/admin/sources` ("Crawl now").
- DataSync removes files from S3 that were deleted on the share (`preserve_deleted_files = REMOVE`); the crawler then
  marks their documents deleted.
- Destroying: RDS and the ALB have deletion protection on. Originals are versioned and stay until the bucket is
  deleted deliberately.

## Smoke test

SSO login, add a source and crawl it, approve a case, generate Word, PowerPoint, PDF and Markdown, ask one research question.
