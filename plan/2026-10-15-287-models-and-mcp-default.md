---
status: draft
issue: 287
spec: spec/2026-10-15-287-models-and-mcp-default.md
---

# Plan: Claude, OpenAI and Gemini as app models; Antigravity, Codex and Claude as MCP clients; MCP on by default

## Approved decisions (from the spec)

- **Model families** stay LiteLLM ids in `EXTRACT_MODEL` and `DRAFT_MODEL`:
  - Claude via Bedrock (`bedrock/eu.anthropic.claude-…`): our-cloud
  - OpenAI (`openai/gpt-5`, `openai/gpt-5-codex`) and Gemini (`gemini/gemini-2.5-pro`): third-party
  - Ollama: unchanged
  - Anthropic's own API is not used.
- **No relabelling.** An override of `destination` to `local` or `our-cloud`
  raises "contradicts" for any model starting `openai/`, `gemini/`,
  `anthropic/` or `vertex_ai/`.
- **Revoked approvals count.** `allowed()` adds `and revoked_by is null`.
- **OpenAI and Gemini request shape.** `PROVIDERS = {"openai/": "OPENAI_API_KEY", "gemini/": "GEMINI_API_KEY"}`. For those prefixes:
  - the mode defaults to `json` (`"mode":"schema"` still selects strict)
  - the system prompt is a plain string, with no `cache_control` block
  - `drop_params=True`
  - `api_key` comes from the provider's own variable, when set
  - `api_base` stays Ollama-only
  - Bedrock and Ollama requests are unchanged.
- **MCP on by default.**
  - `enabled()` is `MCP_ENABLED` not `"0"`.
  - `build()` still refuses to start without issuer, audience and client ids.
  - Settings uses `enabled()` and shows `OPENAI_API_KEY` and `GEMINI_API_KEY` as configured yes or no.
- **Compose:**
  - web: `MCP_ENABLED: ${MCP_ENABLED:-1}`
  - `app` (tests, also CI via `ci.yml:29`): `MCP_ENABLED: "0"`
  - web and worker: pass `OPENAI_API_KEY` and `GEMINI_API_KEY` through.
- **Two-group local tokens (dev only).** The mock claim is
  `["$${groups}", "$${group2}"]`, and `mcp_token.sh` takes `group2`, which
  defaults to the first group.
- **Terraform:**
  - MCP variables, with a precondition that fails `plan` on an incomplete setup
  - the MCP values go into `web_env`
  - a provider key secret is created only when a model uses that prefix, and
    goes to web and worker.
- **Docs:** Codex and Antigravity connection sections, plus model guidance.
  Unverified steps are marked.
- **Split:** steps 1–2 Claude, step 3 Codex, step 4 agy.

## Steps

1. **Model policy and requests (Claude)**
   - **`app/llm.py:50-67` `destination()`.** Add a module constant
     `THIRD_PARTY = ("openai/", "gemini/", "anthropic/", "vertex_ai/")`. In
     the override branch (`:56`), raise "contradicts" when
     `model.startswith(THIRD_PARTY)`, next to the cloud-Ollama check.
   - **`:70-77` `allowed()`.** Add `and revoked_by is null` to the query.
   - **`complete_json`, `:135-170`:**
     - Add `PROVIDERS` next to `THIRD_PARTY`, and set
       `provider = next((v for p, v in PROVIDERS.items() if model.startswith(p)), None)`.
     - Change `json_mode` to default to `"json"` when `ollama or provider`.
     - When `provider`:
       - `extra["drop_params"] = True`
       - when `os.environ.get(provider)` is set, `extra["api_key"]` is that value
     - In `_call`, the system message `content` is `system` (a plain string)
       when `provider`, and the existing `cache_control` block otherwise.
   - **`tests/test_llm.py:80`:** the `openai/gpt` → `local` assert moves into
     the "contradicts" loop, with `gemini/x` → `our-cloud` added beside it.
   - **New tests** (using the existing `litellm.completion` monkeypatch and
     approval fixtures in `tests/test_llm.py`):
     - a revoked approval refuses
     - `openai/gpt-5`:
       - `response_format` json_object
       - a string system content
       - `drop_params`
       - `api_key` equals `OPENAI_API_KEY`
       - the `GEMINI_API_KEY` value is not sent
     - `gemini/…`: the mirror image
     - `openai/…` on confidential data with no approval raises `PolicyError`
     - the existing `test_cache_control_and_strict_schema` stays unchanged.

   Verify by: `docker compose build app && docker compose run --rm app pytest tests/test_llm.py -q`.
   **Negative control:** without the `THIRD_PARTY` check, the relabel test fails.
   Traps:
   - Build the image before pytest (stale-image memory).
   - The approval rows need `revoked_by` set; check the `model_approvals` columns in `app/migrations`.

2. **MCP on by default (Claude)**
   - **`app/mcp_server.py:1063`:** `return os.environ.get("MCP_ENABLED", "1") != "0"`.
     Update any `MCP_ENABLED=1` docstring or comment nearby.
   - **`app/settings.py`:**
     - `:11`: add `"OPENAI_API_KEY", "GEMINI_API_KEY"` to `SECRETS`
     - `:39`: `mcp_enabled = mcp_server.enabled()`, importing it as the module does elsewhere
   - **`docker-compose.yml`:**
     - `:18-19` idp: replace both `"groups": "$${groups}"` with
       `"groups": ["$${groups}", "$${group2}"]`, and update the comment
     - `:54-56` app: `MCP_ENABLED: "0"`, with a comment that MCP tests turn it on themselves
     - `:61-75` worker and `:85-107` web: `OPENAI_API_KEY: ${OPENAI_API_KEY:-}`
       and `GEMINI_API_KEY: ${GEMINI_API_KEY:-}`
     - `:108-109` web: `MCP_ENABLED: ${MCP_ENABLED:-1}`, with the comment "on unless MCP_ENABLED=0"
   - **`scripts/mcp_token.sh`:**
     - usage: `[--review] [username] [group] [group2]`
     - `group2=${3:-$group}`
     - add `--data-urlencode "group2=$group2"`
     - drop the "One group per token" line and give the
       `alice refs-users sales` example
   - **`tests/test_mcp_http.py:89,175`:** `monkeypatch.setenv("MCP_ENABLED", "0")`.
   - **New tests:**
     - `MCP_ENABLED` unset with the other MCP env set: `/mcp` answers (not 404)
     - unset with no issuer: `create_app()` raises `MCP_ISSUER`
   - **`tests/test_settings.py`:** with `MCP_ENABLED` unset and the MCP env
     set, the page shows MCP on, and the two new secrets show as configured
     yes or no only (never the value).
   - **Every other test that relied on MCP being off:** grep
     `create_app()` callers; the compose `app` service's `"0"` covers them.

   Verify by: focused tests (`test_mcp_http.py`, `test_settings.py`,
   `test_mcp*.py`), then the full suite.
   **Negative control:** restoring `== "1"` fails the default-on test.
   Traps:
   - Never two `docker compose run` at once in one worktree.
   - Don't touch the running refsdemo.

3. **Terraform (Codex, worktree `../refs-engine-287-infra` from this branch)**
   - **`infra/variables.tf`** (after `draft_model`, `:146`):
     - `mcp_enabled` (bool, default `true`)
     - `mcp_issuer`, `mcp_audience`, `mcp_client_ids`, `mcp_tenant_id` (string, default `""`)
     - `mcp_scope` (default `"refs.access"`)
   - **Precondition.** Use a `lifecycle.precondition` on the web ECS task
     definition (it runs at plan). When `var.mcp_enabled` is true, it fails
     unless `mcp_issuer` starts with `https://` and `mcp_audience` and
     `mcp_client_ids` are non-empty. The message names `mcp_enabled = false`
     as the way out.
   - **`infra/ecs.tf:37-48` `web_env`:**
     - `MCP_ENABLED = var.mcp_enabled ? "1" : "0"`
     - when enabled: `MCP_ISSUER`, `MCP_AUDIENCE`, `MCP_CLIENT_IDS`,
       `MCP_SCOPE`, plus `MCP_TENANT_ID` when non-empty
   - **`infra/secrets.tf:3`:** `secret_names` becomes the existing set,
     plus `openai-api-key` when `var.extract_model` or `var.draft_model`
     starts with `openai/`, and `gemini-api-key` likewise for `gemini/`.
     Use locals `use_openai` and `use_gemini`.
   - **`infra/ecs.tf:50-66`:** `web_secrets` and `worker_secrets` merge in
     `OPENAI_API_KEY` and `GEMINI_API_KEY` under the same conditions.
   - **`infra/iam.tf:15-17` `exec_secrets`:** `web` and `worker` concat the
     same ARNs under the same conditions.
   - **`infra/terraform.tfvars.example`:**
     - MCP values (Entra v2 issuer `https://login.microsoftonline.com/<tenant>/v2.0`, `api://…` audience)
     - commented model examples (Bedrock, `openai/gpt-5`, `gemini/gemini-2.5-pro`)
   - **`infra/README.md`:** the two new optional secrets, and the MCP
     default-on note.

   Verify by: `terraform fmt -check -recursive` and `terraform init -backend=false && terraform validate`
   in `infra/` (I run these if Codex can't). Try a `terraform plan` with no
   issuer only if it runs without AWS credentials; otherwise validate only,
   and say so.
   Traps:
   - Codex has no Docker or network; no apply.
   - The secret ARN lookups must not index a secret that wasn't created.

4. **Docs (agy, worktree `../refs-engine-287-docs` from this branch)**
   - **`docs/mcp.md`:**
     - MCP is on by default; `MCP_ENABLED=0` turns it off; startup fails without the sign-in values.
     - **Codex section:** `codex mcp add refs --url https://<host>/mcp --oauth-client-id <id>`, then `codex mcp login refs`; fallback `bearer_token_env_var`.
     - **Antigravity section:** `mcp_config.json` with `serverUrl` and `oauth.clientId`; callback `https://antigravity.google/oauth-callback`. The OAuth key name is marked unverified.
     - the Entra redirect URIs per client
     - the two-group local token: `scripts/mcp_token.sh alice refs-users sales`
     - every client step not tried against a real tenant is marked "unverified"
   - **`docs/user-guide/admins.md`:**
     - the model families with example ids
     - OpenAI and Gemini need a Models-page approval for confidential data
     - Bedrock profile ARNs belong in `bedrock_model_arns`
     - prefer a general GPT model over Codex for extraction
     - the provider key secrets.

   Verify by: I read the diff against this step and check every command and
   file:line it cites. agy runs with no shell or URL tools, so the prompt
   starts with the "do not run commands or fetch URLs" line.
   Traps:
   - No `#NNN` in user-facing text beyond the repo's existing style.
   - No document content or file names in examples.

## Tests

```bash
cd ../refs-engine-287 && git merge origin/main && docker compose build app && \
  docker compose run --rm app pytest tests/test_llm.py tests/test_mcp_http.py tests/test_settings.py -q
docker compose run --rm app pytest -q   # full suite: all pass
cd infra && terraform fmt -check -recursive && terraform init -backend=false && terraform validate
```

**Negative controls:** the two named in steps 1 and 2.

**Reviews** (after merging `origin/main`):
- Opus, given the plan and the diff
- Codex, read-only `review --base origin/main`
- agy, in plan mode

**Demo:**
- Restart refsdemo from this worktree with no `MCP_ENABLED` flag; MCP answers.
- `scripts/mcp_token.sh alice refs-users sales`, then `list_latest` and
  `get_case` return demo cases.
- Print counts and ids only.
- The models stay local `qwen3:14b`.

**Not verified here:** live OpenAI, Gemini and Bedrock calls, and real Entra
sign-in. The PR lists the steps, to be run first on public data.

## Rollback

Revert the merge commit. There is no schema or data change.

A deployment that can't supply MCP settings in the meantime sets
`MCP_ENABLED=0` (or `mcp_enabled = false`).

## Implementation notes

(filled in during implementation)
