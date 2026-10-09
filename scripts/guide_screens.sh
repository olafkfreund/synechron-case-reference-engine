#!/usr/bin/env bash
# Makes the user-guide screenshots from a RUNNING local demo (demo data only).
# Does not start or stop anything. Needs curl and chromium.
set -euo pipefail
BASE=${BASE:-http://localhost:8000}
[[ $BASE =~ ^http://localhost(:[0-9]+)?$ ]] || { echo "refusing: BASE must be a localhost URL, got $BASE" >&2; exit 2; }
OUT=docs/user-guide/img
mkdir -p "$OUT"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

login() { # name claims -> $tmp/name.jar
  local jar=$tmp/$1.jar authz cb
  authz=$(curl -s -o /dev/null -c "$jar" -b "$jar" -w '%{redirect_url}' "$BASE/login" || true)
  [ -n "$authz" ] || { echo "login $1: /login gave no redirect" >&2; exit 1; }
  cb=$(curl -s -o /dev/null -c "$jar" -b "$jar" -w '%{redirect_url}' \
    --data-urlencode "username=$1" --data-urlencode "claims=$2" "$authz" || true)
  [ -n "$cb" ] || { echo "login $1: IdP gave no redirect" >&2; exit 1; }
  curl -s -o /dev/null -c "$jar" -b "$jar" "$cb"
}

shot() { # jar png curl-args...
  local jar=$tmp/$1.jar png=$2; shift 2
  curl -sf -b "$jar" "$@" | sed 's#<head[^>]*>#&<base href="'"$BASE"'/">#' > "$tmp/page.html"
  chromium --headless --disable-gpu --hide-scrollbars --window-size=1280,900 \
    --screenshot="$OUT/$png" "file://$tmp/page.html" >/dev/null 2>&1
  echo "wrote $OUT/$png"
}

login admin '{"name": "Test Admin", "groups": ["refs-admins", "sales", "delivery"]}'
login reviewer '{"groups": ["refs-reviewers", "sales", "delivery"]}'
login sales '{"groups": ["refs-users", "sales"]}'

shot sales search.png -X POST -H "Origin: $BASE" --data-urlencode bid_text=payments "$BASE/search"
shot sales research.png "$BASE/research"
shot reviewer review-queue.png "$BASE/review"

# first queued case whose page shows an unsourced field
case_id=
for id in $(curl -sf -b "$tmp/reviewer.jar" "$BASE/review" | grep -o 'href="/review/[^"]*"' | sed 's#.*/review/##; s#"##' | awk '!s[$0]++'); do
  if curl -sf -b "$tmp/reviewer.jar" "$BASE/review/$id" | grep -q unsourced; then case_id=$id; break; fi
done
[ -n "$case_id" ] || { echo "no review case with an unsourced field found" >&2; exit 1; }
shot reviewer review-case.png "$BASE/review/$case_id"

shot admin sources.png "$BASE/admin/sources"
shot admin clients.png "$BASE/admin/clients"
shot admin models.png "$BASE/admin/models"
shot admin audit.png "$BASE/admin/audit"
