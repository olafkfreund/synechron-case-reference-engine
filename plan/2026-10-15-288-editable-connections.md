---
status: approved
issue: 288
spec: spec/2026-10-15-288-editable-connections.md
---

# Plan: admins can edit connection credentials and ids in the portal

Branch `feat/288-editable-connections`, worktree `../refs-engine-288`. This branch already includes #287 (merged at 9bd6e88).

## Approved decisions

**The ten managed names.**

| Name | Kind | Store | Approvals |
|---|---|---|---|
| `GRAPH_CLIENT_ID` + `GRAPH_CLIENT_SECRET` | id + secret, one pair | Postgres + Secrets Manager | one admin |
| `CONFLUENCE_EMAIL` + `CONFLUENCE_TOKEN` | id + secret, one pair | Postgres + Secrets Manager | one admin |
| `BRAVE_API_KEY` | secret | Secrets Manager | one admin |
| `OLLAMA_CLOUD_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` | secret | Secrets Manager | **two admins** |
| `EXTRACT_MODEL`, `DRAFT_MODEL` | one of `MODEL_CHOICES` | Postgres | **two admins** |

> [!NOTE]
> **Count correction.** The spec says "seven secrets" and "nine names". The table above has **six** secrets and **ten** names. There was never an `ollama-cloud-key` in infra, and step 4 adds it. The design is unchanged.

**Terraform-only, read-only on the page with the reason shown:**
- `S3_BUCKET`: the IAM binding
- `OLLAMA_API_BASE`: where data goes
- `*_OPTIONS`
- SSO
- MCP

**Reader.** `app/conn_settings.py` `get(name)` is the only way code reads these ten names.
- **A plain value:** the `connection_settings` row, else the environment.
- **A secret:** `AWSCURRENT` of the id in `SETTINGS_SECRET_IDS`, a JSON `{NAME: id}`.
  - It is never read from the environment.
  - `ResourceNotFound`, no current version, or `""` means "not configured" (`None`).
- **Without `SETTINGS_SECRET_IDS`:** the environment, as today. The page says editing needs Secrets Manager.
- **Cache:** per process, 60 s TTL. A save clears the saving process's cache.
- **Outage** (any other AWS error): the last value is used for at most 10 minutes. After that the read raises. It never falls back to the environment.

**Writes.**
- **One admin:** `put_secret_value`. `AWSCURRENT` moves to the new version and the old one becomes `AWSPREVIOUS`.
- **Two admins:**
  - Proposing: `put_secret_value(VersionStages=["REFS_PENDING"])`.
  - Approving: `update_secret_version_stage(AWSCURRENT → pending version)`, then remove `REFS_PENDING`.
  - Rejecting or withdrawing: remove `REFS_PENDING`.
  - A model proposal stores its new id in the audit row's `new`. Approving it writes `connection_settings`.
- **A pair:** a new id needs its secret in the same save.
  - The secret is written first, then the id row.
  - If the row write fails, `AWSCURRENT` is moved back to the old version and the error is shown.
- **Restore previous:** the secret goes back to the `AWSPREVIOUS` version id; a plain value goes back to the previous `new` in the audit. It follows the same one-admin or two-admin rule.
- **Clear:** a separate button. It writes `""`, which follows the same approval rule.
  - Unverified: whether real Secrets Manager accepts an empty `SecretString`.
  - If staging refuses it, store the reserved value `-` and treat it as not configured. Record that as a deviation.

**Two admins.**
- **One open proposal per name**, held under `pg_advisory_xact_lock(hashtext(name))`.
- **Approve and reject:** any admin except the proposer. The server checks `approver != proposer`.
- **Withdraw:** the proposer only.
- **No expiry.** Shortcut comment: expiry, if stale proposals pile up.

**Audit.** `connection_setting_changes` is append-only.
- **Columns:** `id`, `name`, `action`, `old`, `new`, `secret_version`, `actor`, `at`.
  - `action` is one of `proposed`, `applied`, `approved`, `rejected`, `withdrawn`, `restored`, `cleared`.
  - `old` and `new` hold plain values only. For a secret they are null.
- **Open proposal:** the latest `proposed` row for a name with no later `approved`, `rejected` or `withdrawn` row.
- `roles.sql` revokes update and delete from `refs_app`.
- The Audit page shows the last 100 rows.

**Validation.** Errors name the field, never the value.

| Field | Rule |
|---|---|
| `GRAPH_CLIENT_ID` | a UUID |
| `CONFLUENCE_EMAIL` | an email, at most 254 characters |
| a secret | 1–4096 characters, one line, no control characters |
| a model | exactly one entry of `MODEL_CHOICES` (comma-separated env) |

- **A blank secret field means unchanged.**

**Models.** A third-party model still needs a live approval (`app/llm.py` `allowed()`). A Bedrock model must be in `bedrock_model_arns`; the page says so. `*_OPTIONS` stays per alias.

**Infra.**
- **Secrets:**
  - The six secrets are always created.
  - They are no longer injected into ECS, and `exec_secrets` drops them.
  - `common_env` gains `SETTINGS_SECRET_IDS`, `MODEL_CHOICES`, `GRAPH_CLIENT_ID` and `CONFLUENCE_EMAIL`, so web gets them too.
- **IAM:**
  - The web task role gets Get, Describe, ListSecretVersionIds, Put and UpdateSecretVersionStage on the six ARNs.
  - The worker task role gets Get only.

**Local.**
- **Seeding:** moto (`s3` service) also serves Secrets Manager. `s3-init` creates `refs-local/<name>`, filled from shell values that are set.
- **Web and worker:** they get `SETTINGS_SECRET_IDS` and lose the env pass-through of the six secrets. Otherwise LiteLLM's own env lookup would still find the keys.
- **Tests:** the `app` test service has no `SETTINGS_SECRET_IDS`, and tests use `mock_aws`.

**Split.** At the user's request the coding is split between Claude, Codex and agy:
- Steps 1–3 and 5: **Claude**. These cover security, the write path and compose.
- Step 4: **Codex** (Terraform).
- Step 6: **agy** (docs).

Codex and agy can't reach Docker, so Claude runs all tests.

## Steps

### 1. Reader, schema and audit table (Claude)

- **New `app/conn_settings.py`:**
  - `MANAGED`: the ten names. `SECRET_NAMES` and `TWO_ADMIN` are subsets.
  - `secret_ids()` parses `SETTINGS_SECRET_IDS`.
  - `get(name)` with the cache and outage rules above. Use `boto3.client("secretsmanager")`, as `app/db.py:16` does.
  - `clear_cache()`.
- **`sql/schema.sql`**, after `source_config_changes` (line 232):
  - `connection_settings(name text primary key, value text not null, changed_by text not null, changed_at timestamptz not null default now())`
  - `connection_setting_changes`, with a check on `action`.
- **`sql/roles.sql:21`:** `revoke update, delete on connection_setting_changes from refs_app;`
- **Verify** with the new `tests/test_conn_settings.py`, using `mock_aws` and monkeypatched time:
  - a row beats env for plain values
  - a managed secret ignores env
  - a missing or empty secret is `None`
  - the cache keeps the value until the TTL and reloads after it
  - an outage within 10 minutes returns the last value; after 10 minutes it raises
- **Traps:**
  - Rebuild the `app` image before pytest.
  - Never log a value.

### 2. Callers read through `get()` (Claude)

- **`app/crawl.py`:** `:130` (Graph id and secret) and `:374` (Confluence email and token).
- **`app/research.py`:** `:588` (Brave) and `:698` (`DRAFT_MODEL` prefix).
- **`app/extract.py:180`:** the `EXTRACT_MODEL` prefix.
- **`app/llm.py`:**
  - `:137`: `model = conn_settings.get(alias)`
  - `:161-162`: `OLLAMA_CLOUD_KEY`
  - `:166-167`: the `PROVIDERS` key
- **Verify:**
  - There are no other reads: `grep -rnE 'environ.*(GRAPH_CLIENT|CONFLUENCE_(EMAIL|TOKEN)|BRAVE_API_KEY|OLLAMA_CLOUD_KEY|OPENAI_API_KEY|GEMINI_API_KEY|EXTRACT_MODEL|DRAFT_MODEL)' app/` finds only `settings.py`, which changes in step 3, and the scripts.
  - A test shows that a changed Brave key, Graph secret and model are picked up after the TTL.
  - The existing `test_llm.py`, `test_crawl*` and `test_research*` still pass. Their env-based setup works because there's no `SETTINGS_SECRET_IDS`.
- **Traps:**
  - `scripts/eval_*.py` set `os.environ["EXTRACT_MODEL"]` themselves, so they keep working through the env fallback. Leave them.
  - `profile(alias)` still reads `*_OPTIONS` from the env.

### 3. Write path, routes, page and audit (Claude)

- **`app/conn_settings.py` writes:**
  - `save(conn, actor, form)` for one-admin names and pairs
  - `propose(conn, actor, name, value)`
  - `decide(conn, actor, name, action)` for approved, rejected and withdrawn
  - `restore(conn, actor, name)`
  - `clear(conn, actor, name)`
  - validators
- **`app/settings.py`:**
  - `SECRETS` status comes from `conn_settings.get` for the six managed names. `OIDC_CLIENT_SECRET` stays env.
  - The values come from `get` for the four plain names.
  - Add the open proposals and the last change per name.
  - Add `POST /admin/settings/connections/{group}`, where `group` is one of `sharepoint`, `confluence`, `brave`, `ollama`, `openai`, `gemini` or `models`. It also takes `.../{name}/approve|reject|withdraw|restore|clear`.
  - Every route uses `require("admin")`. CSRF is global (`app/main.py:194`).
  - Redirect 303 to the page. On an error, re-render it with field-name errors.
- **`app/templates/settings.html`:**
  - one form per connector:
    - a password input with `autocomplete="new-password"` for a secret
    - a text input for an id
    - a `<select>` from `MODEL_CHOICES` for a model
  - the status, the last change, a two-admin badge, and a proposal row with its buttons
  - Terraform-only rows stay read-only, with their reason
  - no script
- **`app/audit.py`** and `audit.html`: a "Connection changes" table of the last 100 rows.
- **Verify** with tests in `tests/test_conn_settings.py` and `tests/test_settings.py`:
  - one-admin save
  - **two-admin:**
    - propose, then approve by another admin, takes effect
    - the proposer approving gets 403
    - reject and withdraw
    - a second proposal is refused
  - a pair id without its secret is refused
  - restore previous, for a secret and for a model
  - clear
  - each validation rule
  - a non-admin gets 403
  - **sentinel checks:** a submitted or stored secret never appears in any response or in `connection_setting_changes`
  - `refs_app` can't update or delete audit rows
- **Negative controls:**
  - Remove the self-approve check: its test fails.
  - Add env fallback for a managed secret: the no-fallback test fails.
  - Rebuild the image before each run.
- **Traps:**
  - Every `**bold**` in the user guide must be a template label (`tests/test_user_guide.py`).
  - Never echo form values into an error.

### 4. Terraform (Codex)

- **`infra/secrets.tf`:**
  - Drop `use_openai`, `use_gemini` and `provider_secrets` (lines 3-4 and 23-25).
  - `secret_names` always includes `openai-api-key`, `gemini-api-key` and `ollama-cloud-key`.
  - Add `local.conn_secret_ids = { GRAPH_CLIENT_SECRET = arn, CONFLUENCE_TOKEN = …, BRAVE_API_KEY = …, OLLAMA_CLOUD_KEY = …, OPENAI_API_KEY = …, GEMINI_API_KEY = … }`.
  - Update the line 1 comment: these six may stay empty.
- **`infra/ecs.tf`:**
  - `web_secrets` (60-63) keeps only `SESSION_SECRET` and `OIDC_CLIENT_SECRET`.
  - `worker_secrets` (71-75) becomes `{}`.
  - `common_env` (28) gains:
    - `SETTINGS_SECRET_IDS = jsonencode(local.conn_secret_ids)`
    - `MODEL_CHOICES = join(",", distinct(concat(var.model_choices, [var.extract_model, var.draft_model])))`
    - `GRAPH_CLIENT_ID`
    - `CONFLUENCE_EMAIL`

    The last two move from the worker env, if they're there.
- **`infra/iam.tf`:**
  - `exec_secrets` (15-17):
    - web gets the session and OIDC secrets only
    - the worker gets `[]`
    - keep the `for_each` working when the list is empty, or drop the worker entry
  - The task-role web policy (`data.aws_iam_policy_document.web`) gains the five actions on `values(local.conn_secret_ids)`.
  - The worker policy gains `GetSecretValue` on them.
- **`infra/variables.tf`:** `model_choices`, a `list(string)` defaulting to `[]`. Its description says each Bedrock id also needs its ARN in `bedrock_model_arns`.
- **`terraform.tfvars.example`** gains an example. The README secrets section says that the six are set in the portal or the console, that tasks don't restart, and that the web role can write them.
- **Verify:** `terraform fmt -check` and `terraform validate` in `infra/`.
- **Traps:** no `plan` or `apply` (no AWS). Nothing is destroyed: secrets are only added.

### 5. Local compose (Claude)

- **`docker-compose.yml`:**
  - `s3-init` also creates `refs-local/<NAME>` for the six. Each gets a `SecretString` from `${NAME:-}` when that is set.
  - The six move from the worker and web environments (72-74, 100-102) to `s3-init`.
  - Web and worker get `SETTINGS_SECRET_IDS` (JSON of `NAME → refs-local/NAME`), `AWS_ENDPOINT_URL_SECRETS_MANAGER: http://s3:5000`, the test credentials, and `MODEL_CHOICES`.
  - The `app` test service stays without them.
- **Verify:**
  - `docker compose config` is valid.
  - Demo:
    - set and rotate the Brave key in the portal, then run a research request
    - change a model with two demo admins (the mock IdP, second user)
    - screenshot the page
    - metrics only
- **Traps:**
  - The demo is restarted with `BRAVE_API_KEY=$(cat /run/agenix/api-brave-search)` inline, never printed.
  - Only local `qwen3:14b`.
  - Coders don't touch `refsdemo`.

### 6. Docs (agy)

- **`docs/user-guide/admins.md`:**
  - editing connections
  - the two-admin rule
  - restore and clear
  - the reasons for the Terraform-only rows
- **README local section:** edits are lost when moto restarts.
- **Verify:** `tests/test_user_guide.py`.
- **Traps:** bold only for real template labels. No secret values in examples.

## Tests

```bash
cd ../refs-engine-288
docker compose build app && docker compose run --rm app pytest      # full suite, all pass
docker compose run --rm app pytest tests/test_conn_settings.py tests/test_settings.py -q
(cd infra && terraform fmt -check && terraform validate)
```

**Reviews:** Opus (the plan plus the diff), Codex read-only `review --base origin/main`, and agy in plan mode.

**Not verified here:** real Secrets Manager and IAM, including whether an empty `SecretString` is accepted. The PR lists the staging steps.

## Rollback

- **Code:** revert the PR. Readers fall back to the environment only when `SETTINGS_SECRET_IDS` is unset.
- **Infra:** revert the Terraform too, so that ECS injects the secrets again. The values set in the portal stay in the same secrets, so nothing is lost.
- **Database:** the new tables are left in place, unused.

## Implementation notes

- **Step 1: unmanaged means environment for all ten names.** Without `SETTINGS_SECRET_IDS`, the plain values come from the environment too, not from `connection_settings`, and the page is read-only. Today's tests and a bare `docker run` are unchanged, and editing is never half on.
- **Step 1: clearing stores `-`.** botocore refuses an empty `SecretString` (minimum length 1) before the call is sent, so real AWS would too. A cleared secret holds `conn_settings.CLEARED` (`-`), and `get()` reads it as not configured. Step 3's validation refuses `-` as a submitted value.
- **Step 3: no restore for half a pair.** "Restore previous" is offered only for Brave, the three provider keys and the two models. Restoring only the id or only the secret of SharePoint or Confluence could pair one identity's id with another's secret, which the intent rules out. To roll back a pair, enter both values again.
- **Step 3: secret stage moves are explicit.** Every write stores the new version under `REFS_PENDING` and then moves `AWSCURRENT` with `update_secret_version_stage`. Reads ask for `VersionStage="AWSCURRENT"`. Two reasons:
  - **moto keeps returning its own default version.** It does so after a stage move, and an implicit `put_secret_value` moves the wrong one.
  - **`AWSPREVIOUS` must be the value that was in force.**
- **Step 3: a new secret's first proposal stays pending.** AWS makes the first version of a secret `AWSCURRENT`, so before proposing a never-set secret, the code first stores a cleared (`-`) version.
- **Step 3: errors are a 400/403/409 naming the setting,** like the Sources page, not a re-rendered form.
- **Review fixes (Opus, Codex).**
  - **Changes go live last.** The audit and id rows are written first. Then `_commit_and_activate` moves `AWSCURRENT` and commits. If either step fails, the secrets go back to the versions that were current, so no change is live without its audit row. `REFS_PENDING` is dropped afterwards, as best effort.
  - **A decision names its proposal.** Approve, Reject and Withdraw send the id of the proposal on screen. A proposal that was replaced since the page loaded gets 409.
  - **A save compares under the lock.** It compares ids and models with the database, not the 60 s cache.
  - **Approving a model re-checks it** against `MODEL_CHOICES`.
  - **Restoring a two-admin secret moves `REFS_PENDING` explicitly** (`RemoveFromVersionId`).
  - **The Secrets Manager client** gets its own session (thread-safe), a 2 s connect and 5 s read timeout, and 2 attempts.
  - **IAM:** `ListSecretVersionIds` is dropped because nothing calls it.
  - **The admin guide states the outage behaviour.**
- **Review fixes (agy).**
  - **A model restore re-checks** the target against `MODEL_CHOICES` before proposing it.
  - **Clearing a secret that is already not configured is refused** (409). Clearing it again would push the real previous value out of `AWSPREVIOUS`.
