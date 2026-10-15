import os
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.datastructures import FormData

from app import conn_settings as cs, db, mcp_server
from app.main import ROLES, User, _env_groups, require
from app.review import page

router = APIRouter()
SECRETS = ("GRAPH_CLIENT_SECRET", "CONFLUENCE_TOKEN", "BRAVE_API_KEY", "OLLAMA_CLOUD_KEY", "OIDC_CLIENT_SECRET",
           "OPENAI_API_KEY", "GEMINI_API_KEY")
COUNTS = ("new", "updated", "deleted", "failed")
# (label, form group, settings): one form per connector (#288)
CONNECTORS = (("SharePoint", "sharepoint", ("GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET")),
              ("Confluence", "confluence", ("CONFLUENCE_EMAIL", "CONFLUENCE_TOKEN")),
              ("Brave search", "brave", ("BRAVE_API_KEY",)),
              ("Models", "models", ("EXTRACT_MODEL", "DRAFT_MODEL")),
              ("Ollama Cloud", "ollama", ("OLLAMA_CLOUD_KEY",)),
              ("OpenAI", "openai", ("OPENAI_API_KEY",)),
              ("Gemini", "gemini", ("GEMINI_API_KEY",)))
GROUPS = {group: names for _, group, names in CONNECTORS}
LABELS = {"GRAPH_CLIENT_ID": "Client id", "GRAPH_CLIENT_SECRET": "Client secret", "CONFLUENCE_EMAIL": "Email",
          "CONFLUENCE_TOKEN": "API token", "BRAVE_API_KEY": "API key", "OLLAMA_CLOUD_KEY": "API key", "OPENAI_API_KEY": "API key",
          "GEMINI_API_KEY": "API key", "EXTRACT_MODEL": "Extraction model", "DRAFT_MODEL": "Drafting model"}


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
    secrets = {name: bool(cs.get(name) if name in cs.MANAGED else os.environ.get(name)) for name in SECRETS}
    values = {name: os.environ.get(name, "") for name in ("S3_BUCKET", "OIDC_CLIENT_ID", "OIDC_GROUPS_CLAIM")}
    values.update({name: cs.get(name) or "" for name in cs.PLAIN_NAMES})
    values["OIDC_GROUPS_CLAIM"] = values["OIDC_GROUPS_CLAIM"] or "groups"
    for name in ("OIDC_METADATA_URL", "OIDC_REDIRECT_URI", "OLLAMA_API_BASE"):
        values[name] = public_url(os.environ.get(name, ""))
    mcp_enabled = mcp_server.enabled()
    if mcp_enabled:
        values.update({name: os.environ.get(name, "") for name in
                       ("MCP_ISSUER", "MCP_AUDIENCE", "MCP_SCOPE", "MCP_CLIENT_IDS")})
        values["MCP_ISSUER"] = public_url(values["MCP_ISSUER"])
    roles = [(role, [(group, group in user.groups) for group in sorted(_env_groups(f"ROLE_{role.upper()}_GROUPS"))])
             for role in ROLES]
    with db.connect() as conn:
        rows = conn.execute(
            "select s.id, s.name, s.kind, s.enabled, s.last_run_at, s.last_counts, "
            "(select max(j.updated_at) from jobs j where j.status='failed' and j.kind like 'crawl_%' "
            "and j.payload->>'source_id' = s.id::text) from sources s order by s.name").fetchall()
        changes = conn.execute("select distinct on (name) name, action, actor, at, new, id "
                               "from connection_setting_changes order by name, id desc").fetchall()
    sources = [(sid, name, kind, enabled, last_run, {key: (counts or {}).get(key, 0) for key in COUNTS}, failed_at)
               for sid, name, kind, enabled, last_run, counts, failed_at in rows]
    last = {name: (action, actor, at) for name, action, actor, at, new, cid in changes}
    proposals = {name: (actor, at, new if name in cs.PLAIN_NAMES else None, cid)
                 for name, action, actor, at, new, cid in changes if action == "proposed"}
    return page(request, "settings.html", user, values=values, secrets=secrets, sources=sources, roles=roles,
                mcp_enabled=mcp_enabled, connectors=CONNECTORS, editable=cs.editable(), last=last, proposals=proposals,
                two_admin=cs.TWO_ADMIN, plain=cs.PLAIN_NAMES, model_choices=cs.model_choices(), labels=LABELS,
                configured=[name for name, ok in secrets.items() if ok])


async def raw_form(request: Request) -> FormData:  # app.sources has one too, but importing it here is circular
    return await request.form()


def _change(fn, *args):
    try:
        with db.connect() as conn:  # one transaction: the change and its audit row commit together
            fn(conn, *args)
    except cs.Refused as e:
        raise HTTPException(e.status, str(e)) from None
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/admin/settings/connections/{group}")
def save_connection(group: str, form: FormData = Depends(raw_form), user: User = Depends(require("admin"))):
    if group not in GROUPS:
        raise HTTPException(404, "no such connection")
    return _change(cs.save, user.sub, {name: str(form.get(name, "")) for name in GROUPS[group]})


@router.post("/admin/settings/connections/{name}/{action}")
def change_connection(name: str, action: str, proposal: int = Form(0), user: User = Depends(require("admin"))):
    if name not in cs.MANAGED or action not in ("approve", "reject", "withdraw", "restore", "clear"):
        raise HTTPException(404, "no such setting")
    if action in ("restore", "clear"):
        return _change(cs.restore if action == "restore" else cs.clear, user.sub, name)
    return _change(cs.decide, user.sub, name, action, proposal)  # the proposal the admin saw
