---
status: approved
issue: 287
author: olafkfreund
---

# Intent: Claude, OpenAI and Gemini as app models; Antigravity, Codex and Claude as MCP clients; MCP on by default

## Problem

**The app's AI work runs on one model family.** Extraction and drafting go
through `EXTRACT_MODEL` and `DRAFT_MODEL` (`app/llm.py`), in practice local
Ollama or Bedrock. Pointing them at OpenAI or Google Gemini fails or behaves
badly:

- GPT-5 and Codex reject `temperature=0`.
- OpenAI's strict schema needs every field listed as required, and our
  `Writeup` model doesn't list them all.
- There is no way to supply the provider keys.
- Infra has no secrets for those keys.

**A setting can bypass the approval for confidential data.**
`*_OPTIONS {"destination":"local"}` can relabel a third-party model as local,
and a revoked approval is not checked by its revoke marker.

**AI agents can't easily use the reference engine:**

- The MCP server is off unless someone sets `MCP_ENABLED=1`.
- Infra doesn't configure it at all.
- The docs cover only Claude Code. People also use Google Antigravity and
  OpenAI Codex.

## Proposed outcome

- **Admins can set `EXTRACT_MODEL` and `DRAFT_MODEL`** to a Claude model on
  AWS Bedrock, an OpenAI model or a Gemini model, and extraction and drafting
  work.
- **OpenAI and Gemini count as third-party.** They read confidential data
  only with a live admin approval on the Models page. Claude reaches us only
  through Bedrock, so its data stays in our AWS account.
- **Each provider's key goes only to that provider.** The Settings page shows
  "Configured" or "Not configured", never the key.
- **The MCP server is on by default.** A deployment without the sign-in
  settings refuses to start rather than run `/mcp` open. `MCP_ENABLED=0`
  turns it off.
- **`docs/mcp.md` explains how to connect** Claude Code, Codex and
  Antigravity. Steps not yet tried against a real tenant are marked as such.

## Affected users and systems

- **Code:** `app/llm.py`, `app/mcp_server.py`, `app/settings.py`
- **Config:** `docker-compose.yml`
- **Terraform:** `infra/` (variables, ECS, secrets, IAM)
- **Docs:** `docs/mcp.md` and the admin guide
- **People:**
  - admins, who choose models and approve them
  - users of AI agents
  - the ops team, who must now supply the MCP sign-in values or turn MCP off

## Constraints

- **Claude only through Bedrock;** the Anthropic API is not used.
- **Third-party models never read confidential data without an unexpired,
  unrevoked approval.**
- **No key is stored or shown, and none is sent to another provider or host.**
- **MCP never runs without token checks.** Missing settings mean startup
  fails.
- **No model picker in the portal.** Models stay deployment settings.
- **Claude Desktop and claude.ai access stay out** (#249 phase 5).
- **Coding is split between Claude, Codex and agy.** The reviews are Opus,
  Codex and agy.

## Open questions

None. The scope (both meanings) and the Claude route (Bedrock) were decided
on 2026-10-15.
