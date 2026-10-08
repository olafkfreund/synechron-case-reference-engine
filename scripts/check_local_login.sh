#!/usr/bin/env bash
# Logs in as each local test user through the mock IdP and checks /me roles.
set -euo pipefail
BASE=${BASE:-http://localhost:8000}
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

# name|claims|expected roles (comma-separated, sorted)
users='admin|{"name": "Test Admin", "groups": ["refs-admins"]}|admin,reviewer,user
reviewer|{"groups": ["refs-reviewers"]}|reviewer,user
sales|{"groups": ["refs-users", "sales"]}|user
nobody|{}|'

fail=0
while IFS='|' read -r name claims want; do
  jar=$tmp/$name.jar
  authz=$(curl -s -o /dev/null -c "$jar" -b "$jar" -w '%{redirect_url}' "$BASE/login")
  cb=$(curl -s -o /dev/null -c "$jar" -b "$jar" -w '%{redirect_url}' \
    --data-urlencode "username=$name" --data-urlencode "claims=$claims" "$authz")
  curl -s -o /dev/null -c "$jar" -b "$jar" "$cb"
  me=$(curl -s -b "$jar" "$BASE/me")
  got=$(printf '%s' "$me" | sed -n 's/.*"roles":\[\([^]]*\)\].*/\1/p' | tr -d '"')
  # roles come back sorted: admin,reviewer,user
  [ -z "$got" ] || got=$(printf '%s' "$got" | tr ',' '\n' | sort | paste -sd, -)
  w=$(printf '%s' "$want" | tr ',' '\n' | sort | paste -sd, -)
  extra=""
  if [ "$name" = nobody ]; then
    code=$(curl -s -o /dev/null -b "$jar" -w '%{http_code}' "$BASE/admin/sources")
    [ "$code" = 403 ] || { extra=" (/admin/sources=$code, want 403)"; got="$got!"; }
  fi
  if [ "$got" = "$w" ]; then echo "OK   $name: roles=[$got]"
  else echo "FAIL $name: roles=[$got] want=[$w]$extra"; fail=1; fi
done <<< "$users"
exit $fail
