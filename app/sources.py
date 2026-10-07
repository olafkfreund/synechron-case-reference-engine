import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg.errors import CheckViolation, UniqueViolation
from psycopg.types.json import Jsonb

from app import db
from app.main import User, require
from app.review import page

router = APIRouter()
# which crawler runs a source; upload sources are S3 prefixes too
JOB = {"s3": "crawl_s3", "upload": "crawl_s3", "sharepoint": "crawl_sharepoint"}
REQUIRED = {"s3": ("bucket",), "upload": ("bucket",), "sharepoint": ("tenant_id", "drive_id")}


def groups(raw: str) -> list[str]:
    return list(dict.fromkeys(g.strip() for g in raw.split(",") if g.strip()))


@router.get("/admin/sources")
def sources_page(request: Request, user: User = Depends(require("admin"))):
    with db.connect() as conn:
        rows = conn.execute("select id, kind, name, config, acl_groups, enabled, last_run_at, last_counts "
                            "from sources order by name").fetchall()
    return page(request, "sources.html", user, sources=rows, kinds=sorted(JOB))


@router.post("/admin/sources")
def create(kind: str = Form(), name: str = Form(), config: str = Form(), acl_groups: str = Form(),
           user: User = Depends(require("admin"))):
    try:
        cfg = json.loads(config)
    except ValueError:
        raise HTTPException(400, "config must be JSON") from None
    if kind not in JOB or not isinstance(cfg, dict) or any(not cfg.get(k) for k in REQUIRED[kind]):
        raise HTTPException(400, f"config for {kind} needs: {', '.join(REQUIRED.get(kind, ()))}")
    if not groups(acl_groups):  # an empty ACL would make every document invisible, or worse, mislead
        raise HTTPException(400, "at least one access group is required")
    try:
        with db.connect() as conn:
            conn.execute("insert into sources(kind, name, config, acl_groups) values (%s,%s,%s,%s)",
                         (kind, name.strip(), Jsonb(cfg), groups(acl_groups)))
    except (UniqueViolation, CheckViolation):
        raise HTTPException(400, "invalid or duplicate source") from None
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/{sid}")
def update(sid: int, acl_groups: str = Form(), enabled: bool = Form(False), user: User = Depends(require("admin"))):
    if not groups(acl_groups):
        raise HTTPException(400, "at least one access group is required")
    with db.connect() as conn:
        if not conn.execute("update sources set acl_groups=%s, enabled=%s where id=%s",
                            (groups(acl_groups), enabled, sid)).rowcount:
            raise HTTPException(404, "no such source")
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
