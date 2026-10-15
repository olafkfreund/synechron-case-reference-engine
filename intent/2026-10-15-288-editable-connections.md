---
status: draft
issue: 288
author: olafkfreund
---

# Intent: admins can edit connection credentials and ids in the portal

## Problem

**The Settings page's Connections table can only be read** (#283). To
change a SharePoint, Confluence or Brave credential, or a model provider's
key, an admin has to ask the ops team to:

- update Secrets Manager or a Terraform variable
- redeploy the tasks, because ECS reads secrets only when a task starts.

The requester wants admins to manage these values in the portal, not just
view them.

#283 rejected editing on purpose. It cited secret storage, IAM, two sources
of truth, and lockout risk. This intent reverses part of that decision at
the requester's request, and has to answer those concerns rather than set
them aside.

## Proposed outcome

**Admins can change these values on the Settings page:**

- **SharePoint:** `GRAPH_CLIENT_ID` and `GRAPH_CLIENT_SECRET`, as a pair
- **Confluence:** `CONFLUENCE_EMAIL` and `CONFLUENCE_TOKEN`, as a pair
- **Keys:** `BRAVE_API_KEY`, `OLLAMA_CLOUD_KEY`, `OPENAI_API_KEY` and `GEMINI_API_KEY`
- **Models:** `EXTRACT_MODEL` and `DRAFT_MODEL`, chosen only from a list that
  Terraform sets. Bedrock's IAM allowlist and the model approvals still
  apply.

**How editing behaves:**

- **Secrets are write-only.**
  - The page shows "Configured" or "Not configured", and when the value
    last changed. It never shows the value or part of it.
  - A blank field leaves the value unchanged. Clearing a value is a separate,
    explicit action.
- **No restart is needed.** A saved change reaches the web and worker
  containers within a short, bounded time.
- **Every change is audited:** who, which setting, when, and whether it
  worked. Values are never recorded.
- **A bad credential can be rolled back** to its previous version.

**Still set only in Terraform, with the reason shown on the page:**

- `S3_BUCKET`: the task roles are bound to that bucket.
- `OLLAMA_API_BASE`: a free host URL could send confidential text anywhere.
- the model options (`*_OPTIONS`)
- single sign-on
- MCP

## Affected users and systems

- **Code:**
  - `app/settings.py` and its template
  - every place that reads these values: `app/crawl.py:130,374`,
    `app/research.py:588` and `app/llm.py` (keys and models)
  - `app/audit.py`
- **Infra:**
  - Secrets Manager secrets (`infra/secrets.tf`)
  - task-role IAM (`infra/iam.tf`): the web role can write those secrets;
    the web and worker roles can read them
  - the ECS secret injection that this replaces (`infra/ecs.tf`)
  - a model allowlist variable
- **Local compose:** it has no AWS, so values come from the environment as
  today.
- **People:** admins, the ops team (fewer redeploys), and security (a new
  write path to credentials).

## Constraints

- **Secrets are stored only in Secrets Manager.** None goes to Postgres,
  logs, audit rows, HTML or error text.
- **Only the admin role can edit,** with the existing CSRF protection.
- **Credentials stay with their identity.** A new client id or email needs
  its new secret in the same save.
- **No editable URL or host field,** so confidential data can't be pointed
  at a new destination.
- **The web and worker containers always agree on the values in force,**
  after a short bounded delay.
- **No silent fallback to an old, stale credential** once a managed one
  exists.
- **Models:** a third-party model still needs a live approval before it
  reads confidential data. A Bedrock model must be in the IAM allowlist.
- **Builds on #287's provider keys,** so it lands after #287.

## Open questions

1. **Models.**
   - You chose "editable within a Terraform allowlist".
   - Codex's research advises keeping models Terraform-only: a model id
     decides where confidential prompts go.
   - My suggestion: keep your choice. The allowlist, the IAM allowlist and
     the approval check together cover that risk.
   - Do you agree, or should models stay read-only?
2. **Separation of duties.** Is one admin enough to change a credential, or
   does a change to a connector's identity (a new client id or email) need
   a second admin to approve it? I suggest one admin, with an audit entry.
3. **A "test connection" button.** #283 rejected it as an SSRF risk. Does it
   stay out? I suggest it stays out.
