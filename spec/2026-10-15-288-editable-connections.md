---
status: draft
issue: 288
intent: intent/2026-10-15-288-editable-connections.md
---

# Spec: admins can edit connection credentials and ids in the portal

## Design

### 1. What's editable, and where each value lives

| Setting | Kind | Stored in | Approvals |
|---|---|---|---|
| `GRAPH_CLIENT_ID` + `GRAPH_CLIENT_SECRET` | id + secret, one pair | Postgres + Secrets Manager | one admin |
| `CONFLUENCE_EMAIL` + `CONFLUENCE_TOKEN` | id + secret, one pair | Postgres + Secrets Manager | one admin |
| `BRAVE_API_KEY` | secret | Secrets Manager | one admin |
| `OLLAMA_CLOUD_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` | secret | Secrets Manager | **two admins** |
| `EXTRACT_MODEL`, `DRAFT_MODEL` | one of `MODEL_CHOICES` | Postgres | **two admins** |

These stay Terraform-only, and the page says why:
- `S3_BUCKET`
- `OLLAMA_API_BASE`
- `*_OPTIONS`
- SSO
- MCP

### 2. One reader for all of them: `app/conn_settings.py`

`get(name) -> str | None` is the only way code reads these nine names.

- **A plain value:**
  - the `connection_settings` row (`name` primary key, `value`,
    `changed_by`, `changed_at`) when one exists
  - else the environment, which Terraform sets the same for web and worker
- **A secret:** the secret's `AWSCURRENT` value, read with
  `secretsmanager.get_secret_value`.
  - **Its id** comes from `SETTINGS_SECRET_IDS`, a JSON map `{NAME: secret id}` set by Terraform.
  - **A secret in that map is never read from the environment.** So there's
    no stale fallback, and LiteLLM's own env lookup finds nothing, because
    the key is no longer injected.
  - **"Not configured":** a secret that's missing or empty.
  - **Without `SETTINGS_SECRET_IDS`** (tests, a bare `docker run`): the
    environment, as today. The page then says editing needs Secrets Manager.
- **Caching.** The cache is per process, with a TTL of 60 s, as
  `review.staff_uploads` does. A save clears the saving process's cache.
  Web and worker therefore agree within 60 s.
- **When Secrets Manager can't be reached:**
  - the last value read is used for up to 10 minutes
  - after that, the read raises, and the job fails and retries as today
  - it never falls back to the environment.

**Callers that switch to `get()`:**
- `app/crawl.py:130,374`
- `app/research.py:588`
- `app/llm.py`: the alias's model, `OLLAMA_CLOUD_KEY`, and the `PROVIDERS` keys
- `app/extract.py:180` and `app/research.py:698`
- `app/settings.py`
- any other `os.environ` read of these nine names, which the plan lists

### 3. Writing a secret, with the pending step

**A one-admin change:**
- `put_secret_value` creates the new version and moves `AWSCURRENT` to it.
- Secrets Manager keeps the old version as `AWSPREVIOUS`.

**A two-admin change:**
- `put_secret_value(..., VersionStages=["REFS_PENDING"])`. `AWSCURRENT`
  doesn't move, so nothing changes yet.
- On approval, `update_secret_version_stage` moves `AWSCURRENT` to the
  pending version and removes `REFS_PENDING`.
- The new value never reaches Postgres. Only the version id is recorded.

**A pair** (a new id with its secret): both go in one save. The secret is
written first, then the id row, in the same request. If the second write
fails, the secret's stage is moved back and the error is shown.

**Restore previous** for a setting is itself a change:
- the secret goes back to `AWSPREVIOUS`
- a plain value goes back to the previous value in the audit log
- it follows the same one-admin or two-admin rule.

### 4. Two admins

- **One proposal at a time.** A two-admin setting can have at most one open
  proposal. Another is refused until the first is approved, rejected or
  withdrawn.
- **Approving.** Any other admin can approve or reject it. The proposer can
  withdraw it, but never approve it (`approved_by != proposed_by`, checked
  on the server).
- **Pending proposals show on the Settings page**, with the setting name,
  who proposed it, when, and for a model the new model id. A secret's value
  is never shown.
- **No expiry.** An open proposal stays open until someone decides it.
  Shortcut: add an expiry if stale proposals pile up.

### 5. Audit: `connection_setting_changes`, append-only

Columns:
- `id`, `name`
- `action`: one of `proposed`, `applied`, `approved`, `rejected`,
  `withdrawn`, `restored`
- `old` and `new`: the plain values only, and null for a secret
- `secret_version`
- `actor`, `at`

**It can't be altered.** `sql/roles.sql` revokes update and delete on it from
`refs_app`, as it does for `source_config_changes`. An open proposal is a
`proposed` row with no later decision row for that name. The Audit page
lists the last 100 rows.

### 6. Validation

| Field | Rule |
|---|---|
| `GRAPH_CLIENT_ID` | a UUID |
| `CONFLUENCE_EMAIL` | an email, at most 254 characters |
| a secret | 1–4096 characters, one line, no control characters |
| a model | exactly one entry of `MODEL_CHOICES` |

- **A blank secret field leaves the secret unchanged.** Clearing it is a
  separate button.
- **A new id needs its secret in the same save.**
- **Errors name the field only,** never the submitted value.
- **The form posts** with the existing CSRF protection, on
  `require("admin")` routes.

### 7. Models

- **The choices.** Terraform's `model_choices` (a list) plus `extract_model`
  and `draft_model` become `MODEL_CHOICES`.
- **Picking a model doesn't skip any check:**
  - a third-party model still needs a live approval before it reads
    confidential data (`app/llm.py`)
  - a Bedrock id still needs its ARN in `bedrock_model_arns`. The variable
    description says to list it; a missing one fails the job with
    AccessDenied.
- **`*_OPTIONS` stays per alias, from Terraform.** An option that clashes
  with the new model fails the job loudly, for example a `destination`
  override on `openai/` (#287 guard). That's why the options are documented
  next to the choices.

### 8. Infra

- **`secrets.tf`:**
  - all seven editable secrets are always created
  - `openai-api-key`, `gemini-api-key` and `ollama-cloud-key` exist even
    before a model uses them, so they can be set
  - this replaces #287's conditional creation: nothing is injected anymore,
    so an empty secret no longer blocks a task from starting.
- **`ecs.tf`:**
  - these secrets leave `web_secrets` and `worker_secrets`
  - `common_env` gains `SETTINGS_SECRET_IDS`, `MODEL_CHOICES`, and
    `GRAPH_CLIENT_ID` and `CONFLUENCE_EMAIL` for web too
  - this fixes today's false "Not configured" on web.
- **`iam.tf`:**
  - **web task role:** `GetSecretValue`, `DescribeSecret`,
    `ListSecretVersionIds`, `PutSecretValue` and `UpdateSecretVersionStage`
    on exactly those seven ARNs
  - **worker task role:** `GetSecretValue` on the same ARNs
  - KMS for `aws_kms_key.data` is already granted to both
  - `exec_secrets` drops them.
- **Networking:** the `secretsmanager` VPC endpoint already exists
  (`network.tf:87`).

### 9. Local compose

- **The moto server** (the `s3` service) also serves Secrets Manager.
  `s3-init` creates `refs-local/<name>` for the seven secrets, filled from
  the shell's values (`BRAVE_API_KEY` and the rest) when they are set.
- **Web and worker** get `SETTINGS_SECRET_IDS` pointing at those secrets.
  Editing therefore works locally, and the demo keeps its Brave key.
- **Edits are lost when moto restarts.** That's dev only, and the docs
  say so.
- **The `app` test service** has no `SETTINGS_SECRET_IDS`. Tests use
  moto's `mock_aws` (moto 5.2.3 is in the lock).

### 10. Page

- **Each Connections row** has an input:
  - a password field (`autocomplete="new-password"`) for a secret
  - a text field for an id
  - a select for a model
- **Each row also shows:**
  - its status: Configured / Not configured, last changed (by whom, when)
  - whether it needs two admins
  - an open proposal, with Approve, Reject and Withdraw buttons
- **No script needed.** A form per connector, as on the Sources page.
- **The Terraform-only rows stay read-only,** with the reason.

## Alternatives rejected

- **Secrets in Postgres, KMS-encrypted.** That's a second secret store, and it
  needs app-side envelope encryption. You chose Secrets Manager.
- **Pinning each secret's version in Postgres.** It's atomic across a pair,
  but every read needs Postgres and then Secrets Manager, and an unlabelled
  version can be deprecated by AWS. Staging labels give the same pending
  and rollback with one store per value.
- **Keeping ECS injection and restarting tasks after a save.** The app would
  need ECS rights, it causes downtime, and the injected copy would be stale
  in between.
- **The environment as a fallback for a managed secret.** That is the "old
  credential quietly comes back" failure the intent rules out.
- **Editable `S3_BUCKET`, `OLLAMA_API_BASE` and `*_OPTIONS`.** Ruled out in the
  intent: IAM binding, and where data goes.
- **Two admins for everything.** You chose one admin for SharePoint,
  Confluence and Brave.
- **A test-connection button.** Ruled out in the intent, as an SSRF risk.

## Risks

- **A Secrets Manager outage** fails crawls and model calls after 10
  minutes of cached values. Today an outage only stops new tasks from
  starting.
- **A two-admin setting can't be changed when only one admin exists.** That's
  intended. A break-glass path remains: Terraform, or the AWS console on the
  secret.
- **Picking a Bedrock model that isn't in `bedrock_model_arns`** fails jobs
  with AccessDenied until Terraform is fixed. The page says to check.
- **Merge order:**
  - #288 lands after #287 and reverses #287's conditional provider secrets.
  - Infra plan: the provider secrets are created, and the injected secrets
    leave the task definitions. That means a new task revision. Nothing is
    destroyed.
- **The web task role can now write credentials.** A compromised web task
  could change them: that's the trade the requester chose. It's limited to
  the seven ARNs, audited in CloudTrail and in our own audit table.

## Verification

**Unit (`mock_aws`):**
- `get()`:
  - plain precedence: row over env
  - a managed secret never reads the env
  - the cache TTL
  - an outage within and after 10 minutes
- one-admin save
- **Two-admin:**
  - propose, approve by another admin, takes effect
  - self-approve refused
  - reject and withdraw
  - one open proposal at a time
- **Pairs:** a new id without its secret is refused
- **Restore previous**
- **Validation:** each rule, and errors name the field, not the value

**Route tests:**
- admin only, and CSRF
- the page and errors never contain a submitted or stored secret (sentinel
  values)
- the audit rows have no secret value
- `refs_app` can't update or delete audit rows

**Caller tests:**
- crawl, research and llm read through `get()`
- a changed key is used after the TTL

**Negative controls:**
- without the self-approve check, its test fails
- with env fallback for managed secrets, the no-fallback test fails

**Other checks:**
- **Full suite:** `docker compose build app && docker compose run --rm app pytest`
- **Terraform:** `fmt -check` and `validate`
- **Demo:**
  - set and rotate the Brave key in the portal, then run a research request
    that uses it
  - change a model with two demo admins
  - screenshot the page
  - metrics only

**Not verified here:** real AWS Secrets Manager and IAM. The PR lists the
staging steps.
