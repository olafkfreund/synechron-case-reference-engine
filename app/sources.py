import json
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg.errors import CheckViolation, UniqueViolation
from psycopg.types.json import Jsonb

from app import db
from app.main import User, require
from app.review import page

router = APIRouter()
# which crawler runs a source; upload sources are S3 prefixes too
JOB = {"s3": "crawl_s3", "upload": "crawl_s3", "sharepoint": "crawl_sharepoint",
       "confluence": "crawl_confluence"}
DATA_CLASSES = ("confidential", "sanitised", "public")
REQUIRED = {"s3": ("bucket",), "upload": ("bucket",), "sharepoint": ("tenant_id", "drive_id"),
            "confluence": ("base_url", "spaces")}


def log_class_change(conn, sid, old, new, who):
    conn.execute("insert into source_class_changes(source_id, old_class, new_class, changed_by) values (%s,%s,%s,%s)",
                 (sid, old, new, who))


def log_acl_change(conn, sid, old, new, who):
    conn.execute("insert into source_acl_changes(source_id, old_groups, new_groups, changed_by) values (%s,%s,%s,%s)",
                 (sid, old, new, who))


def groups(raw: str) -> list[str]:
    return list(dict.fromkeys(g.strip() for g in raw.split(",") if g.strip()))


@router.get("/admin/sources")
def sources_page(request: Request, user: User = Depends(require("admin"))):
    with db.connect() as conn:
        rows = conn.execute("select id, kind, name, config, acl_groups, enabled, last_run_at, last_counts, data_class "
                            "from sources order by name").fetchall()
    return page(request, "sources.html", user, sources=rows, kinds=sorted(JOB), classes=DATA_CLASSES)


@router.post("/admin/sources")
def create(kind: str = Form(), name: str = Form(), config: str = Form(), acl_groups: str = Form(),
           data_class: str = Form("confidential"), executed_contracts: bool = Form(False),
           user: User = Depends(require("admin"))):
    try:
        cfg = json.loads(config)
    except ValueError:
        raise HTTPException(400, "config must be JSON") from None
    if kind not in JOB or not isinstance(cfg, dict) or any(not cfg.get(k) for k in REQUIRED[kind]):
        raise HTTPException(400, f"config for {kind} needs: {', '.join(REQUIRED.get(kind, ()))}")
    if data_class not in DATA_CLASSES:
        raise HTTPException(400, "invalid data class")
    if executed_contracts:
        cfg["executed_contracts"] = True
    if not groups(acl_groups):  # an empty ACL would make every document invisible, or worse, mislead
        raise HTTPException(400, "at least one access group is required")
    if kind == "confluence" and (not isinstance(cfg["spaces"], list) or not all(
            isinstance(k, str) and re.fullmatch(r"~?[A-Za-z0-9_-]+", k) for k in cfg["spaces"])):
        raise HTTPException(400, "spaces must be a list of Confluence space keys")
    try:
        with db.connect() as conn:
            sid = conn.execute("insert into sources(kind, name, config, acl_groups, data_class) values (%s,%s,%s,%s,%s) "
                               "returning id", (kind, name.strip(), Jsonb(cfg), groups(acl_groups), data_class)).fetchone()[0]
            if data_class != "confidential":
                log_class_change(conn, sid, None, data_class, user.sub)
    except (UniqueViolation, CheckViolation):
        raise HTTPException(400, "invalid or duplicate source") from None
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/{sid}")
def update(sid: int, acl_groups: str = Form(), enabled: bool = Form(False), data_class: str = Form(""),
           executed_contracts: bool = Form(False), user: User = Depends(require("admin"))):
    if data_class and data_class not in DATA_CLASSES:  # empty: leave it as it is
        raise HTTPException(400, "invalid data class")
    if not groups(acl_groups):
        raise HTTPException(400, "at least one access group is required")
    with db.connect() as conn:  # one transaction: the change and its log row commit together
        old = conn.execute("select data_class, acl_groups from sources where id=%s for update", (sid,)).fetchone()
        if not old:
            raise HTTPException(404, "no such source")
        conn.execute("update sources set acl_groups=%s, enabled=%s, data_class=coalesce(nullif(%s,''), data_class), "
                     "config=jsonb_set(config, '{executed_contracts}', to_jsonb(%s::bool)) "
                     "where id=%s", (groups(acl_groups), enabled, data_class, executed_contracts, sid))
        if groups(acl_groups) != old[1]:  # documents carry a copy of the groups: apply it in the same transaction
            conn.execute("update documents set acl_groups=%s where source_id=%s", (groups(acl_groups), sid))
            log_acl_change(conn, sid, old[1], groups(acl_groups), user.sub)
        if data_class and data_class != old[0]:
            log_class_change(conn, sid, old[0], data_class, user.sub)
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/{sid}/crawl")
def crawl_now(sid: int, user: User = Depends(require("admin"))):
    with db.connect() as conn:
        row = conn.execute("select kind from sources where id=%s and enabled", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "no such enabled source")
        conn.execute("insert into jobs(kind, payload) values (%s, jsonb_build_object('source_id', %s::bigint))",
                     (JOB[row[0]], sid))
    return RedirectResponse("/admin/sources", status_code=303)
