---
status: approved
issue: 287
intent: intent/2026-10-15-287-models-and-mcp-default.md
---

# Spec: Claude, OpenAI and Gemini as app models; Antigravity, Codex and Claude as MCP clients; MCP on by default

## Design

### A. Model calls (`app/llm.py`)

Model ids stay LiteLLM ids in `EXTRACT_MODEL` and `DRAFT_MODEL`. Supported
families:

| Family | Example id | Destination |
|---|---|---|
| Claude on AWS Bedrock | `bedrock/eu.anthropic.claude-sonnet-4-5-20250929-v1:0` | our-cloud, already (`llm.py:60`) |
| OpenAI | `openai/gpt-5`, `openai/gpt-5-codex` | third-party, already (falls through at `:67`) |
| Google Gemini | `gemini/gemini-2.5-pro` | third-party, already |
| Ollama (unchanged) | `ollama_chat/qwen3:14b` | local, or third-party for cloud models |

LiteLLM 1.104.2, which is pinned, lists all of these with response-schema
support. The Codex models run in LiteLLM's "responses" mode, which
`litellm.completion` bridges to OpenAI's Responses API.

**1. A third-party model can't be relabelled.**
- In `destination()`, an override to `local` or `our-cloud` already raises
  "contradicts" for cloud Ollama. It now also does for any model starting
  `openai/`, `gemini/`, `anthropic/` or `vertex_ai/`.
- `tests/test_llm.py:80`, which asserts the bypass works, is inverted.

**2. Revoked approvals count.** `allowed()` adds `and revoked_by is null`,
matching `mcp_server.py:481`.

**3. Request shape per provider.** `PROVIDERS` maps each prefix to its key
variable:

```python
PROVIDERS = {"openai/": "OPENAI_API_KEY", "gemini/": "GEMINI_API_KEY"}
```

For a model with one of these prefixes:
- **The mode defaults to `json`,** the path Ollama uses: `json_object`, the
  schema in the prompt, Pydantic validation and the schema-echo retry. The
  `"mode":"schema"` option in `*_OPTIONS` still selects strict schema.
- **Why not strict schema by default:**
  - OpenAI's strict mode needs every property listed as required, and
    `Writeup` (`writeup.py:16`) has defaulted fields that aren't.
  - Gemini's handling of `$defs` is unverified.
- **The system prompt is plain text.** The `cache_control` content block
  stays for every other model (Bedrock and Ollama, unchanged).
- **`drop_params=True`.** GPT-5 and Codex reject `temperature=0`. LiteLLM
  drops parameters a model doesn't take, and leaves them for the rest.
- **The key is passed explicitly** as `api_key=os.environ[<var>]` when set.
  This follows the `OLLAMA_CLOUD_KEY` pattern (`llm.py:157`), so a key only
  ever goes to its own provider.
- **`api_base` stays Ollama-only,** as now, so no key can be pointed at
  another host.

### B. MCP on by default

- **The switch:** `mcp_server.enabled()` returns `os.environ.get("MCP_ENABLED", "1") != "0"`.
- **Fail-closed stays as it is.** `build()` refuses to start without
  `MCP_ISSUER`, `MCP_AUDIENCE` and `MCP_CLIENT_IDS` (`mcp_server.py:1070-1078`).
- **Settings uses the same switch.** `settings.py:39` calls
  `mcp_server.enabled()` and shows `OPENAI_API_KEY` and `GEMINI_API_KEY` in
  `SECRETS` (configured yes or no only).
- **`docker-compose.yml`:**
  - **web:** `MCP_ENABLED: ${MCP_ENABLED:-1}`, so the demo and local web
    have MCP with no flag. The mock-IdP values are already there.
  - **`app` (tests):** `MCP_ENABLED: "0"`. Many tests call `create_app()`
    with no MCP settings. MCP tests set `MCP_ENABLED=1` themselves.
  - **web and worker:** pass `OPENAI_API_KEY: ${OPENAI_API_KEY:-}` and
    `GEMINI_API_KEY: ${GEMINI_API_KEY:-}` through.
- **Local tokens can carry two groups (dev only).**
  - The mock IdP's claim becomes `"groups": ["$${groups}", "$${group2}"]`.
  - `scripts/mcp_token.sh [--review] [user] [group] [group2]` sends
    `group2`, which defaults to the first group.
  - So `scripts/mcp_token.sh alice refs-users sales` gets a role and sees
    the demo's `sales` cases. Today no local token can see any demo case.
- **Tests:** `tests/test_mcp_http.py:89,175` change `delenv("MCP_ENABLED")`
  to `setenv("MCP_ENABLED", "0")`.

### C. Terraform (`infra/`)

**MCP variables**, in `variables.tf`:
- `mcp_enabled`: bool, default `true`
- `mcp_issuer`: string, default `""`
- `mcp_audience`: string, default `""`
- `mcp_client_ids`: string, default `""`
- `mcp_tenant_id`: string, default `""`
- `mcp_scope`: string, default `"refs.access"`

**A precondition fails `plan` early.** A `check` or resource precondition
fails it when `mcp_enabled` is true and the issuer isn't `https://…`, or the
audience or client ids are empty.

**Web environment.** `ecs.tf` adds to `web_env`:
- `MCP_ENABLED = var.mcp_enabled ? "1" : "0"`
- the other MCP values, which are public ids, not secrets

**Provider keys:**
- **When a key is created.** In `secrets.tf`, `openai-api-key` is added to
  `secret_names` only when `var.extract_model` or `var.draft_model` starts
  with `openai/`, and `gemini-api-key` likewise for `gemini/`. A task can't
  start while a secret it uses is empty, so a key that isn't used must not
  exist.
- **Where it goes.** A created key goes to `web_secrets` and
  `worker_secrets` in `ecs.tf`, and to both lists in `exec_secrets` in
  `iam.tf:14`. Both containers call the models: `search.py:133` and
  `research.py:112` run in web.

**Examples.** `terraform.tfvars.example` gains MCP and model examples.

### D. Docs

**`docs/mcp.md`:**
- MCP is on by default; `MCP_ENABLED=0` turns it off.
- New sections for **Codex** (`codex mcp add … --oauth-client-id`,
  `codex mcp login`, with a bearer-token fallback) and **Antigravity**
  (`mcp_config.json` with `serverUrl` and `oauth.clientId`, and the
  redirect URI).
- The Entra redirect URIs each client needs.
- The two-group local token.
- Every client step not run against a real tenant is marked "unverified".

**Admin guide (`docs/user-guide/admins.md`):**
- the supported model families and example ids
- OpenAI and Gemini need a Models-page approval for confidential data
- Bedrock profile ARNs belong in `bedrock_model_arns`
- prefer a general GPT model over Codex for extraction

### Split

| Part | Owner |
|---|---|
| A, B | Claude |
| C | Codex |
| D | agy |

Codex and agy work in their own worktrees after A and B are committed. I
run all the tests.

## Alternatives rejected

- **Claude through Anthropic's API.** The user chose Bedrock: data stays in
  our account.
- **A model picker in the portal.** The web and worker containers must agree
  on the model, and the IAM allowlist must match. This was deferred in #283.
- **Strict schema for OpenAI and Gemini, made to work by marking every field
  required in `llm_schema()`.** It changes the prompt for every model,
  including the measured Ollama extraction. Json mode is already proven, and
  strict stays one option away.
- **Turning MCP on silently, or quietly off, when its settings are
  missing.** An open `/mcp` or a hidden outage are both worse than a refused
  start.
- **Always creating both provider secrets.** An empty secret stops the task
  from starting.

## Risks

- **Turning MCP on by default changes existing deployments.** A deployment
  without the MCP settings now refuses to start (web), and Terraform fails
  at plan. That is intended; the fix is to set the values or
  `mcp_enabled = false`. The PR says so.
- **No live call has been made to OpenAI, Gemini or Bedrock** from here. It
  needs keys and Bedrock access, and this session uses no cloud models. Live
  checks go in the PR, to run first on public data such as research claims.
- **Json mode is less strict than schema mode.** Pydantic validation and the
  one retry still guard the output. A bad reply fails the job as today.
- **Codex models in extraction are untested** and not recommended.
- **Real Entra sign-in from Codex and Antigravity is unverified,** as is the
  key name in Antigravity's OAuth config (the research sources disagreed).
- **The mock's literal `${group2}`.** The mock may leave that text in place
  when no second group is sent. It's harmless: unknown groups are dropped by
  `known_groups()`, and the script always sends one.

## Verification

**`tests/test_llm.py`:**
- `openai/x` and `gemini/x` relabelled as `local` or `our-cloud` raise
  "contradicts"
- a revoked approval refuses
- `openai/gpt-5`:
  - json mode by default
  - a plain system string
  - `drop_params=True`
  - `api_key` taken from `OPENAI_API_KEY`
  - the Gemini key is not sent
- `gemini/…`: the mirror image of the OpenAI checks
- `bedrock/…`: unchanged (`test_cache_control_and_strict_schema`)
- `openai/…` reading confidential data without an approval raises
  `PolicyError`

**MCP tests:**
- with `MCP_ENABLED` unset, `/mcp` is mounted
- set but with no issuer, startup fails
- `MCP_ENABLED=0` turns it off
- Settings shows MCP on

**Negative controls:**
- removing the prefix guard fails the relabel test
- restoring `== "1"` fails the default-on test

**Full suite:** `docker compose build app && docker compose run --rm app pytest`.

**Terraform:**
- `terraform fmt -check` and `terraform validate` in `infra/`, with no apply
- a `plan` with `mcp_enabled = true` and no issuer fails at the precondition,
  where it can run without AWS credentials; otherwise validate only

**Demo** (`scripts/demo.sh up`):
- MCP answers with no flag
- `scripts/mcp_token.sh alice refs-users sales` lists and reads demo cases
  through `list_latest` and `get_case`
- only counts and ids are printed
- the demo keeps the local `qwen3:14b`
