import os
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request

from app import db
from app.main import ROLES, User, _env_groups, require
from app.review import page

router = APIRouter()
SECRETS = ("GRAPH_CLIENT_SECRET", "CONFLUENCE_TOKEN", "BRAVE_API_KEY", "OLLAMA_CLOUD_KEY", "OIDC_CLIENT_SECRET")
COUNTS = ("new", "updated", "deleted", "failed")


def public_url(value: str) -> str:
    if not value:
        return ""
    try:
        url = urlsplit(value)
        if not url.scheme or not url.hostname:
            return ""
        host = url.hostname
        if ":" in host:
            host = f"[{host}]"
        return f"{url.scheme}://{host}{f':{url.port}' if url.port is not None else ''}{url.path}"
    except ValueError:
        return ""


@router.get("/admin/settings")
def settings_page(request: Request, user: User = Depends(require("admin"))):
    secrets = {name: bool(os.environ.get(name)) for name in SECRETS}
    values = {name: os.environ.get(name, "") for name in (
        "GRAPH_CLIENT_ID", "CONFLUENCE_EMAIL", "S3_BUCKET", "EXTRACT_MODEL", "DRAFT_MODEL",
        "OIDC_CLIENT_ID", "OIDC_GROUPS_CLAIM")}
    values["OIDC_GROUPS_CLAIM"] = values["OIDC_GROUPS_CLAIM"] or "groups"
    for name in ("OIDC_METADATA_URL", "OIDC_REDIRECT_URI", "OLLAMA_API_BASE"):
        values[name] = public_url(os.environ.get(name, ""))
    mcp_enabled = os.environ.get("MCP_ENABLED") == "1"
    if mcp_enabled:
        values.update({name: os.environ.get(name, "") for name in
                       ("MCP_ISSUER", "MCP_AUDIENCE", "MCP_SCOPE", "MCP_CLIENT_IDS")})
    roles = [(role, [(group, group in user.groups) for group in sorted(_env_groups(f"ROLE_{role.upper()}_GROUPS"))])
             for role in ROLES]
    with db.connect() as conn:
        rows = conn.execute(
            "select s.id, s.name, s.kind, s.enabled, s.last_run_at, s.last_counts, "
            "(select max(j.updated_at) from jobs j where j.status='failed' and j.kind like 'crawl_%' "
            "and (j.payload->>'source_id')::bigint = s.id) from sources s order by s.name").fetchall()
    sources = [(sid, name, kind, enabled, last_run, {key: (counts or {}).get(key, 0) for key in COUNTS}, failed_at)
               for sid, name, kind, enabled, last_run, counts, failed_at in rows]
    return page(request, "settings.html", user, values=values, secrets=secrets, sources=sources, roles=roles,
                mcp_enabled=mcp_enabled)
