#!/bin/sh
# DEV ONLY. Prints an access token for /mcp from the local mock IdP (docker compose up idp web).
# The mock mints any user and group on request, with no secret: never point this at a real IdP.
#   scripts/mcp_token.sh [username] [group]      e.g. scripts/mcp_token.sh alice refs-users
# One group per token (the mock fills the claim from one request parameter). refs-admins implies reviewer and user.
# IDP_URL overrides the issuer URL; it must equal MCP_ISSUER of the app.
set -eu
user=${1:-dev-user}
group=${2:-refs-users}
idp=${IDP_URL:-http://idp.localhost:8080/refs-mcp}
curl -fsS "$idp/token" -d grant_type=client_credentials -d client_id=local-dev -d client_secret=dev-only-not-a-secret \
  -d scope=refs.access --data-urlencode "username=$user" --data-urlencode "groups=$group" |
  sed -n 's/.*"access_token" *: *"\([^"]*\)".*/\1/p'
