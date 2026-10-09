import json
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg.errors import CheckViolation, UniqueViolation
from psycopg.types.json import Jsonb

from app import db
from app.main import UPLOAD_MAGIC, User, require, upload_cap
from app.ingest import reopen_merged
from app.review import page

router = APIRouter()
# which crawler runs a source; upload sources are S3 prefixes too
JOB = {"s3": "crawl_s3", "upload": "crawl_s3", "sharepoint": "crawl_sharepoint",
       "confluence": "crawl_confluence"}
NOTICES = {  # allow-list: the query string picks a message, never supplies one
    "uploaded": ("ok", "File queued for crawl"),
    "type": ("bad", f"Upload refused: allowed types are {', '.join(sorted(UPLOAD_MAGIC))}"),
    "size": ("bad", "Upload refused: the file is larger than the limit"),
    "content": ("bad", "Upload refused: the file content does not match its extension"),
    "nosource": ("bad", "Upload refused: no enabled upload source is configured"),
    "source": ("bad", "Upload refused: that source is not an enabled upload source"),
}
DATA_CLASSES = ("confidential", "sanitised", "public")
REQUIRED = {"s3": ("bucket",), "upload": ("bucket",), "sharepoint": ("tenant_id", "drive_id"),
            "confluence": ("base_url", "spaces")}

# #64: flipping "contracts executed" re-triages what is already crawled
QUEUE_CONTRACTS = """insert into jobs(kind, payload)
    select 'extract', jsonb_build_object('document_id', d.id, 'basis', 'engagement', 'basis_reason', 'source marked executed')
    from documents d where d.source_id=%s and d.kind='contract' and d.deleted_at is null
    and not exists (select 1 from cases c where c.document_id=d.id)
    and not exists (select 1 from jobs j where j.kind='extract' and j.status in ('queued','running')
                    and (j.payload->>'document_id')::bigint=d.id)"""
RETIRE_FLAGGED = """update cases c set status='rejected' from documents d
    where d.id=c.document_id and d.source_id=%s and c.basis='engagement'
    and c.data->>'basis_reason'='source marked executed' and c.status<>'rejected'
    returning c.document_id"""
DROP_FLAGGED_JOBS = """delete from jobs j using documents d
    where j.kind='extract' and j.status='queued' and (j.payload->>'document_id')::bigint=d.id
    and d.source_id=%s and j.payload->>'basis_reason'='source marked executed'"""


def log_class_change(conn, sid, old, new, who):
    conn.execute("insert into source_class_changes(source_id, old_class, new_class, changed_by) values (%s,%s,%s,%s)",
                 (sid, old, new, who))


def log_acl_change(conn, sid, old, new, who):
    conn.execute("insert into source_acl_changes(source_id, old_groups, new_groups, changed_by) values (%s,%s,%s,%s)",
                 (sid, old, new, who))


def groups(raw: str) -> list[str]:
    return list(dict.fromkeys(g.strip() for g in raw.split(",") if g.strip()))


@router.get("/admin/sources")
def sources_page(request: Request, notice: str = "", source: int | None = None,
                 job: int | None = None, user: User = Depends(require("admin"))):
    with db.connect() as conn:
        rows = conn.execute("select id, kind, name, config, acl_groups, enabled, last_run_at, last_counts, data_class "
                            "from sources order by name").fetchall()
    done = notice == "uploaded"
    return page(request, "sources.html", user, sources=rows, kinds=sorted(JOB), classes=DATA_CLASSES,
                notice=NOTICES.get(notice), job=job if done else None,
                source_name=next((r[2] for r in rows if r[0] == source), None) if done else None,
                upload_cap=upload_cap(), upload_types=sorted(UPLOAD_MAGIC))


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
        old = conn.execute("select data_class, acl_groups, "
                           "coalesce(config->'executed_contracts' = 'true', false) from sources where id=%s for update", (sid,)).fetchone()
        if not old:
            raise HTTPException(404, "no such source")
        conn.execute("update sources set acl_groups=%s, enabled=%s, data_class=coalesce(nullif(%s,''), data_class), "
                     "config=jsonb_set(config, '{executed_contracts}', to_jsonb(%s::bool)) "
                     "where id=%s", (groups(acl_groups), enabled, data_class, executed_contracts, sid))
        if executed_contracts and not old[2]:  # contracts already crawled become engagements too (#64)
            conn.execute(QUEUE_CONTRACTS, (sid,))
        elif old[2] and not executed_contracts:  # and stop being engagements, approved ones included
            reopen_merged(conn, [r[0] for r in conn.execute(RETIRE_FLAGGED, (sid,))])
            conn.execute(DROP_FLAGGED_JOBS, (sid,))
        # documents carry a copy of the groups: apply it in the same transaction, on every save, so a
        # re-save also repairs copies a crawl wrote back before #58
        conn.execute("update documents set acl_groups=%s where source_id=%s and acl_groups is distinct from %s",
                     (groups(acl_groups), sid, groups(acl_groups)))
        if groups(acl_groups) != old[1]:
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
