import re
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg.errors import CheckViolation, UniqueViolation
from psycopg.types.json import Jsonb

from app import db
from app.extract import EXTRACTION_VERSION
from app.main import UPLOAD_MAGIC, User, require, upload_cap, wants_html
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
    "reextract": ("ok", "Older cases queued for re-extraction; they go back to review"),
    "capability": ("ok", "Capability drafts queued; they go to review"),
    "crawling": ("bad", "A crawl is running for this source; try again when it finishes"),
}
# #222: cases extracted before the current EXTRACTION_VERSION; a merged parent has no document, so the join drops it
REEXTRACT_BATCH = 25
REEXTRACT_LOCK = 222_001  # fixed advisory-lock key for the bulk re-extract
OLDER = """from cases c join documents d on d.id = c.document_id
    where coalesce((c.data->>'extraction_version')::int, 1) < %s and c.status <> 'rejected' and d.deleted_at is null
    and c.merged_into is null  -- members are re-extracted through their parent's reopen path
    and not exists (select 1 from jobs f where f.kind='extract' and f.status='failed' and f.updated_at > now() - interval '24 hours'
                    and (f.payload->>'document_id')::bigint = d.id)  -- a failing case must not take every batch
    and not exists (select 1 from jobs j where j.kind='extract' and j.status in ('queued','running')
                    and (j.payload->>'document_id')::bigint = d.id)"""
REEXTRACT = ("insert into jobs(kind, payload) select 'extract', jsonb_build_object('document_id', d.id, 'basis', c.basis, "
             "'basis_reason', coalesce(c.data->>'basis_reason', ''), 'checksum', d.checksum) " + OLDER + " order by c.id limit %s")
# #213: proposals, decks and contracts triage skipped before capability documents existed (live, no case, nothing queued)
SKIPPED = """from documents d where d.kind in ('proposal','deck','contract') and d.deleted_at is null
    and not exists (select 1 from cases c where c.document_id = d.id)
    and not exists (select 1 from jobs j where j.kind='extract' and (j.payload->>'document_id')::bigint = d.id)"""  # any status: a failed one is not skipped
CAPABILITY_DRAFTS = ("insert into jobs(kind, payload) select 'extract', jsonb_build_object('document_id', d.id, 'basis', 'capability', "
                     "'basis_reason', '', 'checksum', d.checksum) " + SKIPPED + " order by d.id limit %s")
DATA_CLASSES = ("confidential", "sanitised", "public")
# #283: the config keys each kind has. The root names the source and is fixed once created (an upload
# source's prefix is where staff files land); the scope can be edited
ROOT = {"s3": ("bucket",), "upload": ("bucket", "prefix"), "sharepoint": ("tenant_id", "drive_id"),
        "confluence": ("base_url",)}
SCOPE = {"s3": ("prefix", "include_ext"), "upload": ("include_ext",), "sharepoint": ("include_ext",),
         "confluence": ("spaces", "api_prefix")}
DEFAULTS = {"prefix": "", "api_prefix": "/wiki"}  # what the crawlers use for an absent key (crawl.py)


def field(key: str, raw: str):
    """One config value from its form text, or None to leave the key out (the crawler default)."""
    v = raw.strip()
    ok = False
    if key == "bucket":
        ok = bool(re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", v))
    elif key == "prefix":
        ok = not v.startswith("/") and ".." not in v.split("/") and len(v) <= 1024
    elif key == "include_ext":
        v = [e.strip().lower().lstrip(".") for e in v.split(",") if e.strip()] or None
        ok = v is None or all(re.fullmatch(r"[a-z0-9]{1,10}", e) for e in v)
    elif key == "tenant_id":
        try:
            v, ok = str(uuid.UUID(v)), True
        except ValueError:
            pass
    elif key == "drive_id":
        ok = bool(re.fullmatch(r"[A-Za-z0-9!_-]{1,200}", v))
    elif key == "base_url":
        try:
            u = urlsplit(v)
            ok = (u.scheme == "https" and bool(u.hostname) and not (u.username or u.password or u.query or u.fragment)
                  and u.port in (None, 443))
        except ValueError:
            pass
        v = v.rstrip("/")
    elif key == "spaces":
        v = [k.strip() for k in v.split(",") if k.strip()]
        ok = bool(v) and all(re.fullmatch(r"~?[A-Za-z0-9_-]+", k) for k in v)
    elif key == "api_prefix":
        ok = v in ("", "/wiki")
    if not ok:
        raise HTTPException(400, f"invalid {key}")
    return v


def parse_config(kind: str, fields: dict[str, str]) -> dict:
    """A kind's config from typed form fields: only the named keys can exist, so none can hold a secret (#283)."""
    if kind not in JOB:
        raise HTTPException(400, "invalid kind")
    return {k: v for k in ROOT[kind] + SCOPE[kind] if (v := field(k, fields.get(k) or "")) is not None}

# #64: flipping "contracts executed" re-triages what is already crawled
QUEUE_CONTRACTS = """insert into jobs(kind, payload)
    select 'extract', jsonb_build_object('document_id', d.id, 'basis', 'engagement', 'basis_reason', 'source marked executed',
                                         'checksum', d.checksum)
    from documents d where d.source_id=%s and d.kind='contract' and d.deleted_at is null
    and not exists (select 1 from cases c where c.document_id=d.id and c.retired_by is distinct from 'untick')
    and not exists (select 1 from jobs j where j.kind='extract' and j.status in ('queued','running')
                    and (j.payload->>'document_id')::bigint=d.id)"""
RETIRE_FLAGGED = """update cases c set status='rejected', retired_by='untick' from documents d
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


def log_config_change(conn, sid, action, old, new, who):
    conn.execute("insert into source_config_changes(source_id, action, old, new, changed_by) values (%s,%s,%s,%s,%s)",
                 (sid, action, old and Jsonb(old), new and Jsonb(new), who))


def groups(raw: str) -> list[str]:
    return list(dict.fromkeys(g.strip() for g in raw.split(",") if g.strip()))


@router.get("/admin/sources")
def sources_page(request: Request, notice: str = "", source: int | None = None,
                 job: int | None = None, queued: int | None = None, total: int | None = None, user: User = Depends(require("admin"))):
    with db.connect() as conn:
        rows = conn.execute("select id, kind, name, config, acl_groups, enabled, last_run_at, last_counts, data_class, "
                            "greatest(0, floor(extract(epoch from now() - last_run_at) / 86400))::int from sources order by name").fetchall()
        older = conn.execute("select count(*) " + OLDER, (EXTRACTION_VERSION,)).fetchone()[0]
        skipped = conn.execute("select count(*) " + SKIPPED).fetchone()[0]
    done = notice == "uploaded"
    return page(request, "sources.html", user, sources=rows, kinds=sorted(JOB), classes=DATA_CLASSES,
                notice=NOTICES.get(notice), older=older, skipped=skipped, queued=queued if notice in ("reextract", "capability") else None, total=total, job=job if done else None,
                source_name=next((r[2] for r in rows if r[0] == source and r[1] == "upload"), None) if done else None,
                upload_cap=upload_cap(), upload_types=sorted(UPLOAD_MAGIC))


@router.post("/admin/reextract")
def reextract(user: User = Depends(require("admin"))):
    with db.connect() as conn:
        # two clicks at once would both see the same cases as unqueued and queue them twice; the lock lasts until commit
        conn.execute("select pg_advisory_xact_lock(%s)", (REEXTRACT_LOCK,))
        total = conn.execute("select count(*) " + OLDER, (EXTRACTION_VERSION,)).fetchone()[0]
        queued = conn.execute(REEXTRACT, (EXTRACTION_VERSION, REEXTRACT_BATCH)).rowcount
    return RedirectResponse(f"/admin/sources?notice=reextract&queued={queued}&total={total}", status_code=303)


@router.post("/admin/capability-drafts")
def capability_drafts(user: User = Depends(require("admin"))):
    with db.connect() as conn:
        conn.execute("select pg_advisory_xact_lock(%s)", (REEXTRACT_LOCK,))  # two clicks must not queue twice
        total = conn.execute("select count(*) " + SKIPPED).fetchone()[0]
        queued = conn.execute(CAPABILITY_DRAFTS, (REEXTRACT_BATCH,)).rowcount
    return RedirectResponse(f"/admin/sources?notice=capability&queued={queued}&total={total}", status_code=303)


@router.post("/admin/sources")
def create(kind: str = Form(), name: str = Form(), acl_groups: str = Form(),
           data_class: str = Form("confidential"), executed_contracts: bool = Form(False), staff: bool = Form(False),
           bucket: str = Form(""), prefix: str = Form(""), include_ext: str = Form(""), tenant_id: str = Form(""),
           drive_id: str = Form(""), base_url: str = Form(""), spaces: str = Form(""), api_prefix: str = Form("/wiki"),
           user: User = Depends(require("admin"))):
    cfg = parse_config(kind, {"bucket": bucket, "prefix": prefix, "include_ext": include_ext, "tenant_id": tenant_id,
                              "drive_id": drive_id, "base_url": base_url, "spaces": spaces, "api_prefix": api_prefix})
    if data_class not in DATA_CLASSES:
        raise HTTPException(400, "invalid data class")
    if executed_contracts:
        cfg["executed_contracts"] = True
    if staff and kind == "upload":
        cfg["staff"] = True
    if not groups(acl_groups):  # an empty ACL would make every document invisible, or worse, mislead
        raise HTTPException(400, "at least one access group is required")
    try:
        with db.connect() as conn:
            sid = conn.execute("insert into sources(kind, name, config, acl_groups, data_class) values (%s,%s,%s,%s,%s) "
                               "returning id", (kind, name.strip(), Jsonb(cfg), groups(acl_groups), data_class)).fetchone()[0]
            if data_class != "confidential":
                log_class_change(conn, sid, None, data_class, user.sub)
            log_config_change(conn, sid, "create", None, {"name": name.strip(), "config": cfg}, user.sub)
    except (UniqueViolation, CheckViolation):
        raise HTTPException(400, "invalid or duplicate source") from None
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/{sid}")
def update(request: Request, sid: int, acl_groups: str = Form(), enabled: bool = Form(False), data_class: str = Form(""),
           executed_contracts: bool = Form(False), name: str | None = Form(None), prefix: str | None = Form(None),
           include_ext: str | None = Form(None), spaces: str | None = Form(None), api_prefix: str | None = Form(None),
           user: User = Depends(require("admin"))):
    if data_class and data_class not in DATA_CLASSES:  # empty: leave it as it is
        raise HTTPException(400, "invalid data class")
    if not groups(acl_groups):
        raise HTTPException(400, "at least one access group is required")
    if name is not None and not name.strip():
        raise HTTPException(400, "a name is required")
    posted = {"prefix": prefix, "include_ext": include_ext, "spaces": spaces, "api_prefix": api_prefix}
    with db.connect() as conn:  # one transaction: the change and its log row commit together
        # #283: the key every crawler holds for its whole run; an edit never races a crawl
        if not conn.execute("select pg_try_advisory_xact_lock(2, %s)", (sid,)).fetchone()[0]:
            if wants_html(request):
                return RedirectResponse("/admin/sources?notice=crawling", status_code=303)
            raise HTTPException(409, "a crawl is running for this source")
        old = conn.execute("select data_class, acl_groups, coalesce(config->'executed_contracts' = 'true', false), "
                           "kind, name, config, enabled from sources where id=%s for update", (sid,)).fetchone()
        if not old:
            raise HTTPException(404, "no such source")
        kind, old_name, old_cfg, was_enabled = old[3:]
        cfg = {**old_cfg, "executed_contracts": executed_contracts}  # unknown stored keys and the root are kept
        for k in SCOPE[kind]:  # absent from the form: unchanged; the root is never read from it
            if posted[k] is not None and (v := field(k, posted[k])) != old_cfg.get(k, DEFAULTS.get(k)):  # the form
                # always posts its fields: the crawler's default for an absent key is no change
                if v is None:
                    cfg.pop(k, None)
                else:
                    cfg[k] = v
        new_name = name.strip() if name is not None else old_name
        rescope = any(cfg.get(k) != old_cfg.get(k) for k in SCOPE[kind])  # a full crawl applies the new scope
        try:
            conn.execute("update sources set acl_groups=%s, enabled=%s, data_class=coalesce(nullif(%s,''), data_class), "
                         "config=%s, name=%s, cursor=case when %s then null else cursor end "
                         "where id=%s", (groups(acl_groups), enabled, data_class, Jsonb(cfg), new_name, rescope, sid))
        except UniqueViolation:
            raise HTTPException(400, "a source with that name exists") from None
        if (new_name, cfg) != (old_name, {**old_cfg, "executed_contracts": old[2]}):  # the flag was always written
            log_config_change(conn, sid, "config", {"name": old_name, "config": old_cfg},
                              {"name": new_name, "config": cfg}, user.sub)
        if enabled != was_enabled:
            log_config_change(conn, sid, "enable" if enabled else "disable", None, None, user.sub)
        # documents carry a copy of the groups: apply it in the same transaction, on every save, so a
        # re-save also repairs copies a crawl wrote back before #58. Documents before cases, the order
        # ingest and extract lock them in, so the untick below cannot deadlock with a running extract (#132)
        conn.execute("update documents set acl_groups=%s where source_id=%s and acl_groups is distinct from %s",
                     (groups(acl_groups), sid, groups(acl_groups)))
        if executed_contracts and not old[2]:  # contracts already crawled become engagements too (#64)
            conn.execute(QUEUE_CONTRACTS, (sid,))
        elif old[2] and not executed_contracts:  # and stop being engagements, approved ones included
            reopen_merged(conn, [r[0] for r in conn.execute(RETIRE_FLAGGED, (sid,))])
            conn.execute(DROP_FLAGGED_JOBS, (sid,))
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
