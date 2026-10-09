#!/usr/bin/env bash
# Local demo with made-up data that survives restarts.   scripts/demo.sh up | reset | down
# Uses only the compose project "refsdemo"; never touches refsdev or any other project.
set -euo pipefail
cd "$(dirname "$0")/.."
# the demo's own model settings win over anything exported in the caller's shell (no stray cloud model)
set -a; . ./.env.demo; set +a
# web research needs a Brave key: taken from agenix when present, never printed (an exported one wins)
[ -z "${BRAVE_API_KEY:-}" ] && [ -r /run/agenix/api-brave-search ] && export BRAVE_API_KEY="$(cat /run/agenix/api-brave-search)"

dc() { docker compose -p refsdemo --env-file .env.demo -f docker-compose.yml -f docker-compose.demo.yml "$@"; }

up() {
  if [ -z "$(dc ps -q web 2>/dev/null)" ] && ss -ltn 2>/dev/null | grep -qE '(127\.0\.0\.1|0\.0\.0\.0|\*|\[::\]|::):8000 '; then
    echo "Port 8000 is taken by something else. Stop it first (this script only manages project refsdemo)." >&2
    exit 1
  fi
  if ! curl -s -m 3 localhost:11434/api/tags >/dev/null; then
    echo "No model server at localhost:11434: starting without a model."
    echo "Live upload extraction and drafting will not work; the seeded cases and search will."
    export EXTRACT_MODEL='' DRAFT_MODEL='' EXTRACT_MODEL_OPTIONS='' DRAFT_MODEL_OPTIONS=''  # shell beats --env-file
  fi
  dc build web worker
  dc up -d web worker
  for _ in $(seq 60); do
    curl -sf localhost:8000/healthz >/dev/null && break
    sleep 2
  done
  curl -sf localhost:8000/healthz >/dev/null || { echo "web did not become healthy" >&2; exit 1; }
  dc exec -T worker python scripts/seed_demo.py  # worker has S3_BUCKET; web deliberately does not (#71)
  cat <<MSG

Demo ready: http://localhost:8000   (login at idp.localhost:8080; see README "Local login")
  admin     {"name": "Test Admin", "groups": ["refs-admins", "sales", "delivery"]}
  reviewer  {"groups": ["refs-reviewers", "sales", "delivery"]}
  sales     {"groups": ["refs-users", "sales"]}
  nobody    {}
MSG
}

case "${1:-}" in
  up) up ;;
  reset) dc down -v; up ;;
  down) dc down ;;
  *) echo "usage: scripts/demo.sh up | reset | down" >&2; exit 2 ;;
esac
