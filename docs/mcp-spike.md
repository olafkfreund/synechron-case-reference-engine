# MCP spike (#249, phase 0)

Done on 2026-10-13. This records what the SDK and our app actually do, so
that phase 1 builds on facts.

## SDK

- **Version:** `mcp==2.3.0` (the latest 2.x), pinned in `requirements.lock`.
  It runs on Python 3.12 against the locked fastapi 0.143.0 and starlette
  1.7.0, and no existing version moved.
- **New transitive dependencies:** `mcp-types`, `httpx2`, `httpcore2`,
  `pyjwt`, `sse-starlette`, `truststore`.
- **Token checks:** `joserfc` (authlib 1.8 deprecates `authlib.jose`). It is
  declared in `pyproject.toml`.

## Mount

- **Path:** `MCPServer.streamable_http_app()` with `streamable_http_path="/"`
  and `stateless_http=True`, mounted at `/mcp`. A POST to `/mcp` is served
  directly, with no redirect.
- **Lifespan:** the parent app's lifespan enters `session_manager.run()`. A
  mounted app's lifespan doesn't run on its own.
- **Off by default:** `/mcp` exists only with `MCP_ENABLED=1`. Without it,
  `/mcp` gives 404.
- **Guard:** the portal's CSRF check is skipped for `/mcp` and `/mcp/...`.
  The 1 MiB body cap still applies, and the SDK's `transport_security`
  checks Host and Origin.

## Protocol versions

- **Both answer `tools/list`:** 2025-06-18 and 2026-07-28 both work on the
  same endpoint, in tests.
- **What 2026-07-28 requests need:**
  - `params._meta` with `io.modelcontextprotocol/protocolVersion` and
    `io.modelcontextprotocol/clientCapabilities`
  - an `Mcp-Method` header matching the body's method

## Authorization

- **401 challenge:** an unauthenticated call gets 401 with
  `WWW-Authenticate: Bearer … resource_metadata="{origin}/.well-known/oauth-protected-resource/mcp"`.
- **Protected resource metadata (PRM):** the SDK serves it only inside the
  mount, at `/mcp/.well-known/oauth-protected-resource/mcp`, but its
  challenge names the **root** URL, which would give 404.
  - We add a root route built with the SDK's
    `create_protected_resource_routes`.
  - The test follows the challenge URL and gets 200, with
    `authorization_servers = [MCP_ISSUER]`.
- **Verifier:** RS256 only, with the JWKS cached for 1 h and refetched on an
  unknown `kid`. It checks `iss`, `aud`, `exp` (60 s leeway), that `iat` is
  present, `tid` when set, and `scp`.
  - **App-only token** (with `roles`, but no `scp`): 401.
  - **Wrong or missing scope:** 403 `insufficient_scope`, from the SDK
    (`required_scopes`).
- **Negative controls:** removing the audience check, the `iat` check, or
  `required_scopes` each fails exactly its test.

## Entra (pending tenant)

These were not run, because no dev Entra tenant is available yet.

- Real token acquisition by Claude Code (`--client-id`) and VS Code.
- Whether `oid`, `azp`, `scp`, `iat`, `tid` and `groups` are present in
  real tokens.
- Whether Entra accepts the RFC 8707 `resource` parameter.

Phase 1 proceeds against the mock IdP. `docs/mcp.md` marks the Entra steps
"pending tenant" until this section is filled in.

## Configuration

See `.env.example`. `MCP_ISSUER` and `MCP_AUDIENCE` are required when
MCP is enabled; the app refuses to start without them. `MCP_TENANT_ID`,
`MCP_SCOPE` (default `refs.access`), `MCP_JWKS_URL` (default: the issuer's
keys URL) and `MCP_RESOURCE_URL` (default `$APP_ORIGIN/mcp`) are optional.
