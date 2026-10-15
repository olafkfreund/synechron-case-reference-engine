#!/bin/sh
# DEV ONLY. Prints an access token for /mcp from the local mock IdP (docker compose up idp web).
# The mock mints any user and group on request, with no secret: never point this at a real IdP.
#   scripts/mcp_token.sh [--review] [username] [group] [group2]   e.g. scripts/mcp_token.sh alice refs-users sales
# --review: the reviewer client, with the refs.review scope too (the seed approves it for confidential data).
# Two groups per token: a role group and an access group (group2 defaults to group). refs-admins implies reviewer and user.
# IDP_URL overrides the issuer URL; it must equal MCP_ISSUER of the app.
set -eu
client=local-dev scope=refs.access
if [ "${1:-}" = --review ]; then client=local-dev-review scope='refs.access refs.review'; shift; fi
user=${1:-dev-user}
group=${2:-refs-users}
group2=${3:-$group}
idp=${IDP_URL:-http://idp.localhost:8080/refs-mcp}
curl -fsS "$idp/token" -d grant_type=client_credentials -d client_id=$client -d client_secret=dev-only-not-a-secret \
  --data-urlencode "scope=$scope" --data-urlencode "username=$user" --data-urlencode "groups=$group" --data-urlencode "group2=$group2" |
  sed -n 's/.*"access_token" *: *"\([^"]*\)".*/\1/p'
