import time
import httpx
import hashlib
import re
import copy
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pathlib import Path
from psycopg.errors import ForeignKeyViolation
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app import anonymise, db, diagrams
from app.extract import MAX_CHARS, check
from app.main import User, require
from app.completeness import CHECKS, checklist, meets_minimum, score
from app import archdiagram, writeup
from app.schema import (CITE_POINTS, POINT_FIELDS, ArchCallout, ArchComponent, ArchFlow, Architecture, Diagram, Outcome, Period, Prose,
                        ReferenceCase, Sourced, _norm, check_architecture, plain)
from app.writeup import PRICE_FLAG, check_prose, quotes_of, result_fields

CAPABILITY_NOTE = "Capability document: describes what we offer, not delivery evidence."  # render imports review, so the text lives here (#213)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")  # autoescape on
# the stylesheet URL changes with its content, so a browser never pairs new pages with an old cached CSS (#90)
templates.env.globals["css_version"] = hashlib.sha256(
    b"".join((Path(__file__).parent / "static" / f).read_bytes() for f in ("portal.css", "theme.js", "review.js"))).hexdigest()[:12]


_staff_seen = (0.0, False)


def staff_uploads() -> bool:
    """Whether the Upload tab shows: a staff upload source exists (#249). Cached, so a page render opens no connection."""
    global _staff_seen
    # shortcut: up to 60 s stale after an admin toggles staff, and per process; a TTL is enough for a nav tab
    if time.monotonic() - _staff_seen[0] < 60 and _staff_seen[0]:
        return _staff_seen[1]
    from app import ingest
    with db.connect() as conn:
        _staff_seen = (time.monotonic(), ingest.staff_source(conn) is not None)
    return _staff_seen[1]


templates.env.globals["staff_uploads"] = staff_uploads

# extraction notes that need no action, with what they mean; anything else is shown as needing attention (#259)
INFO_NOTES = (("read in ", "The document was long, so it was read in parts. Nothing to do."),
              (" malformed or unknown item(s) skipped", "The model returned an item that failed validation; it was dropped. Check nothing important is missing."))


def split_notes(notes):
    """(action notes, [(info note, explanation)])"""
    info = lambda n: next((e for k, e in INFO_NOTES if n.startswith(k) or (k.startswith(" ") and n.endswith(k))), None)
    return [n for n in notes if not info(n)], [(n, info(n)) for n in notes if info(n)]


PROSE_LABELS = {"challenge": "Challenge", "solution": "Solution", "implementation": "Implementation approach",
                "configuration": "Configuration and integration"}
SCALARS = ("title", "client_mention", "industry", "region", "engagement_type", "challenge",
           "solution", "implementation", "configuration", "lessons_learned", "duration_months", "team_size")
WRITABLE = ("implementation", "configuration", "lessons_learned")  # a reviewer may write these by hand (#198)
INTS = ("duration_months", "team_size")
LISTS = ("capabilities", "tech_stack", "tech_references", "outcomes")
# a case can be edited/approved/rejected only while it is new or its approval has expired
OPEN = "(c.status = 'extracted' or (c.status = 'approved' and c.review_due <= now() + interval '30 days'))"
# the queue lists only new and expired cases; due-soon ones have their own list (#143)
QUEUE = "(c.status = 'extracted' or (c.status = 'approved' and c.review_due < now()))"
# need-to-know: every document behind a case must be live and open to the user;
# a member is never shown on its own while merged (its content is inside the merged case)
VISIBLE = ("c.merged_into is null and coalesce(c.member_count, 1) = (select count(*) from cases m "
           "join documents md on md.id = m.document_id where (m.id = c.id or m.merged_into = c.id) "
           "and md.deleted_at is null and md.acl_groups && %s::text[])")
# review pages only: a withdrawn member must not trap its merged case (single documents stay hidden) (it can still be un-merged)
REVIEWABLE = VISIBLE.replace("md.deleted_at is null", "(md.deleted_at is null or c.member_count is not null)")
# the version a reviewer saw; a re-extraction in between must not be approved unseen
VERSION = "md5(c.data::text)"


def page(request, name, user, **ctx):
    return templates.TemplateResponse(request, name, {"user": user, **ctx})


def load(conn, cid, user, version):
    """Lock the case row and return (case, document text; {document_id: text} for a merged case).

    404 if the reviewer cannot see it (never confirm it exists), 409 if it changed since the
    page was loaded or someone else already decided it.
    """
    groups = list(user.groups)
    row = conn.execute(
        f"select c.data, d.text from cases c left join documents d on d.id = c.document_id "
        f"where c.id = %s and {VISIBLE} and {OPEN} and {VERSION} = %s for update of c",
        (cid, groups, version)).fetchone()
    if not row:
        seen = conn.execute(
            f"select {VERSION} = %s from cases c left join documents d on d.id = c.document_id "
            f"where c.id = %s and {VISIBLE}", (version, cid, groups)).fetchone()
        if not seen:
            raise HTTPException(404, "no such case")
        raise HTTPException(409, "case is not open for review" if seen[0] else "case changed; reload the page")
    if row[1] is None:  # a merged case: each member document vouches for its own items
        texts = {d: t[:MAX_CHARS] for d, t in conn.execute(
            "select m.document_id, md.text from cases m join documents md on md.id = m.document_id "
            "where m.merged_into = %s order by m.id", (cid,))}
        return ReferenceCase.model_validate(row[0]), texts
    return ReferenceCase.model_validate(row[0]), row[1][:MAX_CHARS]


def fix_summary(case):
    if not case.summary_sourced():
        case.summary = ""
        note = "summary used numbers absent from the quotes; blanked"
        if note not in case.needs_attention:
            case.needs_attention = [*case.needs_attention, note]


def save(conn, cid, case):
    conn.execute(
        "update cases set data = %s, summary = %s, search_text = %s where id = %s",
        (Jsonb(case.model_dump()), case.summary, case.search_text(), cid))


def count_unsourced(o):
    if isinstance(o, dict):
        return int(o.get("unsourced") is True) + sum(map(count_unsourced, o.values()))
    return sum(map(count_unsourced, o)) if isinstance(o, list) else 0


def rows(case, docs=None):
    def row(path, label, obj, names):  # docs: {document_id: title}, for a merged case
        return dict(path=path, label=label, quote=obj.source_quote, unsourced=obj.unsourced,
                    doc=(docs or {}).get(obj.document_id, ""),
                    written=getattr(obj, "by_reviewer", False), writable=path in WRITABLE,
                    inputs=[(n, "" if getattr(obj, n) is None else getattr(obj, n)) for n in names])
    out = [row(n, n.replace("_", " "), getattr(case, n), ["value"]) for n in SCALARS
           if not (n == "lessons_learned" and case.basis != "delivered")]  # as outcomes (#198)
    def new(n, label, names):
        return dict(path=f"{n}.new", label=label, quote="", unsourced=False, new=True, inputs=[(i, "") for i in names])
    for n, label in (("capabilities", "capability"), ("tech_stack", "tech"), ("tech_references", "technical reference")):
        out += [dict(row(f"{n}.{i}", f"{label if n == 'tech_references' else n.replace('_', ' ')} #{i + 1}", o, ["value"]), item=True)
                for i, o in enumerate(getattr(case, n))]
        out.append(new(n, f"add {label}", ["value"]))
    out += [dict(row(f"outcomes.{i}", f"outcome #{i + 1}", o, ["metric", "value"]), item=True) for i, o in enumerate(case.outcomes)]
    if case.basis == "delivered": out.append(new("outcomes", "add outcome", ["metric", "value"]))  # contracted scope claims no results (#139)
    out.append(row("period", "period", case.period, ["start", "end"]))
    out.append(dict(path="summary", label="summary", quote="", unsourced=False, no_quote=True,
                    inputs=[("value", case.summary)]))
    return out


@router.get("/")
def landing(request: Request, user: User = Depends(require("user"))):
    from app.search import browse  # app.search imports app.review: a top-level import would be circular
    latest = browse(user, {}, "", "newest", 1)[:5]
    queue = waiting = None
    with db.connect() as conn:
        research = conn.execute("select id, query, status, retrieved_at from research where created_by=%s "
                                "order by retrieved_at desc limit 5", (user.sub,)).fetchall()
        if "reviewer" in user.roles:
            queue = conn.execute(f"select count(*) from cases c where {REVIEWABLE} and {QUEUE}", (list(user.groups),)).fetchone()[0]
            waiting = conn.execute("select count(*) from jobs where kind='extract' and status in ('queued','running')").fetchone()[0]
    clients = anonymise.load_clients()
    research = [(i, "[withheld]" if anonymise.blocked(q, clients) else q, st, at) for i, q, st, at in research]  # as research_view
    return page(request, "landing.html", user, latest=latest, research=research, queue=queue, waiting=waiting)


@router.get("/review")
def review_list(request: Request, user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        found = conn.execute(
            f"select c.id, c.status, c.data, d.title, c.member_count from cases c left join documents d on d.id = c.document_id "
            f"where {REVIEWABLE} and {QUEUE} order by c.id", (list(user.groups),)).fetchall()
        # reminders: approvals that expire within 30 days (the list above only has the expired ones)
        soon = conn.execute(
            "select c.id, c.data, d.title, c.review_due, c.member_count from cases c left join documents d on d.id = c.document_id "
            f"where {REVIEWABLE} and c.status = 'approved' and c.review_due > now() "
            "and c.review_due <= now() + interval '30 days' order by c.review_due", (list(user.groups),)).fetchall()
    cases = [dict(id=i, status=s, title=d["title"]["value"], document=t,
                  attention=len(split_notes(d.get("needs_attention", []))[0]), unsourced=count_unsourced(d), members=n)
             for i, s, d, t, n in found]
    due_soon = [dict(id=i, title=d["title"]["value"], document=t, due=due.strftime("%Y-%m-%d"), members=n) for i, d, t, due, n in soon]
    return page(request, "review_list.html", user, cases=cases, due_soon=due_soon)


SKIP_CONTRACT = "Skipped: contract not marked executed"
RETRIAGED = "No longer delivery evidence (document changed)"  # ingest retired it, no reviewer did (#218)


def doc_status(kind, case_status, retired_by, merged_into, job):
    """(label, link) for one document row: the newest extract job first, then the case, then 'no case' by kind."""
    if job in ("queued", "running"):
        return "Waiting for extraction", None
    if job == "failed" and case_status is None:
        return "Extraction failed", None
    if merged_into:
        return "Part of a merged case", f"/review/{merged_into}"
    if case_status == "extracted":
        return "Case waiting for review", "case"
    if case_status == "approved":
        return "Approved", "case"
    if case_status == "rejected":
        if retired_by == "untick":
            return SKIP_CONTRACT, None
        return (RETRIAGED if retired_by == "retriage" else "Rejected in review"), "case"
    if kind == "contract":
        return SKIP_CONTRACT, None
    if kind in ("proposal", "deck"):
        return "Skipped: proposal or deck, not delivered work", None
    return "Skipped: not a case document", None


@router.get("/documents")
def documents(request: Request, pageno: int = Query(1, alias="page"), user: User = Depends(require("reviewer"))):
    pageno = min(max(pageno, 1), 1000)
    with db.connect() as conn:
        # shortcut: offset paging; switch to keyset past ~50k documents
        found = conn.execute(
            "select d.id, d.title, d.kind, d.created_at, d.updated_at, s.name, c.id, c.status, c.retired_by, c.merged_into, j.status "
            "from documents d join sources s on s.id = d.source_id left join cases c on c.document_id = d.id "
            "left join lateral (select status from jobs where kind = 'extract' and payload->>'document_id' = d.id::text "
            "order by id desc limit 1) j on true "
            "where d.deleted_at is null and d.acl_groups && %s::text[] "
            f"order by d.updated_at desc, d.id desc limit 50 offset {(pageno - 1) * 50}", (list(user.groups),)).fetchall()
    rows = []
    for did, title, kind, created, updated, source, cid, cstatus, retired, merged, job in found:
        label, link = doc_status(kind, cstatus, retired, merged, job)
        rows.append(dict(title=title, source=source, updated=updated.strftime("%Y-%m-%d"),
                         first=created.strftime("%Y-%m-%d") if created.date() != updated.date() else "",
                         label=label, link=f"/review/{cid}" if link == "case" else link,
                         sources=label == SKIP_CONTRACT))
    return page(request, "documents.html", user, rows=rows, pageno=pageno, full=len(rows) == 50)


@router.get("/review/{cid}")
def review_detail(cid: int, request: Request, user: User = Depends(require("reviewer"))):
    return _detail(cid, request, user)


def _detail(cid, request, user, arch_form=None):
    with db.connect() as conn:
        r = conn.execute(
            f"select c.data, c.status, d.title, d.external_id, s.name, {OPEN}, {VERSION}, c.member_count, c.basis, "
            f"case when c.review_due > now() then c.review_due end, c.public_sources, md5(c.public_sources::text), "
            f"(select u.user_name from uploads u where u.source_id = d.source_id and u.s3_key = d.external_id limit 1) "
            f"from cases c left join documents d on d.id = c.document_id left join sources s on s.id = d.source_id "
            f"where c.id = %s and {REVIEWABLE}", (cid, list(user.groups))).fetchone()
        registry = anonymise.load_clients(conn)
        merged = r and r[7] is not None
        members = conn.execute(
            "select m.id, d.title, s.name, d.external_id, m.data->>'basis_reason', m.status, d.deleted_at is not null, d.id, "
            "md5(m.data::text) is distinct from m.merged_version "
            "from cases m join documents d on d.id = m.document_id join sources s on s.id = d.source_id "
            "where m.merged_into = %s order by m.id", (cid,)).fetchall() if merged else []
        remerge = bool(merged and any(m[6] or m[8] for m in members) and sum(not m[6] for m in members) >= 2)
        cands = candidates(conn, cid, user) if r and r[5] and r[7] is None and r[8] == "engagement" else []
    if not r:
        raise HTTPException(404, "no such case")
    case = ReferenceCase.model_validate(r[0])  # the document text is deliberately not shown
    notes, infos = split_notes(case.needs_attention)
    # organisations come from extraction; no LLM call on page view
    from app.research import visible_claims  # here: research imports this module
    shown = {c["n"]: c for c in visible_claims([{**c, "n": i} for i, c in enumerate(r[10])], registry)[0]}
    sources = [shown.get(i) or {"n": i, "hidden": True} for i in range(len(r[10]))]  # a hidden one can still be removed
    unlisted = anonymise.unlisted([*case.organisations, case.client_mention.value or ""], registry) if r[5] else []
    found = []
    if r[5]:  # reviewable: candidates from the documents are offered, never shown elsewhere
        with db.connect() as conn:
            found = candidates_of(conn, cid, user, case)
    sections = [dict(f=f, label=PROSE_LABELS[f], prose=case.prose.get(f), points=case.points.get(f, []), min=CHECKS.get(f"{f}_words"))
                for f in POINT_FIELDS if case.points.get(f) or f in case.prose]
    checks = checklist(case)
    return page(request, "review_detail.html", user, arch=_arch_panel(case, arch_form), id=cid, checks=checks, minimum=meets_minimum(checks), rows=rows(case, {m[7]: m[1] for m in members}), notes=notes, infos=infos,
                sections=sections, found=found, max_diagrams=diagrams.MAX_PER_CASE,
                unlisted=unlisted, diagrams=case.diagrams, members=members, cands=cands, remerge=remerge,
                basis=case.basis, basis_reason=case.basis_reason, capability_note=CAPABILITY_NOTE, status=r[1], document=r[2],
                external_id=r[3], source=r[4], merged=bool(members),
                reviewable=r[5] and not any(m[6] for m in members), v=r[6], sources=sources, sources_v=r[11],
                uploaded_by=r[12], due=r[9].strftime("%Y-%m-%d") if r[9] else None)  # in date by the database clock, as search judges it


def apply_edit(conn, user, cid, v, field, value=None, metric=None, start=None, end=None, quote=None,
               written=False, action="save"):
    """One reviewer edit, shared by the portal route and the MCP tool; raises HTTPException. Returns the new version."""
    # `unsourced` is never read from the form: check() recomputes it against the document
    value, metric, start, end, quote = (s.strip() if s is not None else None for s in (value, metric, start, end, quote))
    case, text = load(conn, cid, user, v)
    name, _, idx = field.partition(".")
    try:
        if action not in ("save", "remove"):
            raise HTTPException(400, "unknown action")
        if action == "remove" and not (name in LISTS and idx and idx != "new"):
            raise HTTPException(400, "unknown field")
        if (written and field not in WRITABLE) or (field == "lessons_learned" and case.basis != "delivered"):
            raise HTTPException(400, "unknown field")
        if field == "summary":
            case.summary = value or ""
            obj = None
        elif name == "prose":  # the reviewer's own write-up: checked against the quotes, never against the document
            if idx not in POINT_FIELDS:
                raise HTTPException(400, "unknown field")
            if value:
                case.prose[idx] = Prose(text=value, by_reviewer=True, flags=check_prose(value, quotes_of(case, idx), case.quotes(), offering=case.basis == "capability"))
            else:
                case.prose.pop(idx, None)  # empty removes it
            obj = None
        elif name in SCALARS and not idx:
            obj = getattr(case, name)
            if value is not None:
                obj.value = (int(value) if value.strip() else None) if name in INTS else (value or None)
            if written:  # the reviewer's own text: no quote, never checked against the document (#198)
                if not value:
                    raise ValueError
                obj.by_reviewer, obj.source_quote, obj.document_id = True, "", None
            elif name in WRITABLE:
                obj.by_reviewer = False
        elif name in LISTS and idx == "new":
            if not (value or "").strip() or not (quote or "").strip() or (name == "outcomes" and not (metric or "").strip()) \
                    or (name == "outcomes" and case.basis != "delivered"):
                raise ValueError
            obj = Outcome(metric=metric, value=value) if name == "outcomes" else Sourced[str](value=value)
            getattr(case, name).append(obj)
        elif name in LISTS and idx and action == "remove":
            if int(idx) < 0:
                raise IndexError
            del getattr(case, name)[int(idx)]
            obj = None
        elif name in LISTS and idx:
            if int(idx) < 0:
                raise IndexError
            obj = getattr(case, name)[int(idx)]
            if isinstance(obj, Outcome):
                if not value or case.basis != "delivered":  # empty: removed, not saved (#108); engagement and capability: remove only (#139)
                    raise ValueError
                obj.metric, obj.value = metric or "", value or ""
            elif value is not None:
                obj.value = value or None
        elif field == "period":
            obj = case.period
            obj.start, obj.end = start or None, end or None
        else:
            raise HTTPException(400, "unknown field")
        if obj is not None and quote is not None and not written:
            if _norm(quote) != _norm(obj.source_quote or ""):  # line breaks or spacing are not a new quote
                obj.document_id = None  # a new quote has no origin yet: check() finds the member that sources it (#128)
            obj.source_quote = quote
        case = ReferenceCase.model_validate(case.model_dump())  # re-run validators (summary length)
    except (ValueError, IndexError, ValidationError):  # no input echoed back
        raise HTTPException(400, "invalid value")
    check(case, text)
    fix_summary(case)
    save(conn, cid, case)
    conn.execute("update cases set status='extracted', approved_by=null, approved_at=null, review_due=null "
                 "where id=%s and status='approved' and review_due > now()", (cid,))  # unreviewed edits never go live (#143)
    return conn.execute("select md5(data::text) from cases where id=%s", (cid,)).fetchone()[0]


@router.post("/review/{cid}/edit")
def edit(cid: int, field: str = Form(), value: str | None = Form(None), metric: str | None = Form(None),
         start: str | None = Form(None), end: str | None = Form(None), quote: str | None = Form(None),
         written: bool = Form(False), action: str = Form("save"), v: str = Form(), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        apply_edit(conn, user, cid, v, field, value, metric, start, end, quote, written, action)
    return RedirectResponse(f"/review/{cid}", status_code=303)


def _caption(conn, caption):
    caption = caption.strip()
    if not 1 <= len(caption) <= diagrams.MAX_CAPTION:
        raise HTTPException(400, f"a caption is 1 to {diagrams.MAX_CAPTION} characters")
    if anonymise.blocked(caption, anonymise.load_clients(conn)):
        raise HTTPException(400, "the caption names a protected client")  # never which one
    return caption


def _reopen(conn, cid):  # any diagram change: unreviewed edits never go live (#143)
    conn.execute("update cases set status='extracted', approved_by=null, approved_at=null, review_due=null "
                 "where id=%s and status='approved' and review_due > now()", (cid,))


# lock order everywhere: the case row (load) before any diagrams row
@router.post("/review/{cid}/diagrams")
def add_diagram(cid: int, file: UploadFile, caption: str = Form(), v: str = Form(),
                user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        case, _ = load(conn, cid, user, v)
        caption = _caption(conn, caption)
        raw = file.file.read(diagrams.MAX_BYTES + 1)  # the decoding is the worker's job
        if len(raw) > diagrams.MAX_BYTES:
            raise HTTPException(413, "the image is larger than 5 MB")
        if not raw.startswith(diagrams.MAGIC):
            raise HTTPException(400, "only PNG and JPEG images are accepted")
        _queue_diagram(conn, cid, case, raw, caption, user)
    return RedirectResponse(f"/review/{cid}", status_code=303)


def _queue_diagram(conn, cid, case, raw, caption, user, image_id=None, credit="", research_image_id=None, rights=None):
    """A new pending diagram for the worker to decode (#200); the case row is locked by the caller."""
    if len(case.diagrams) >= diagrams.MAX_PER_CASE:
        raise HTTPException(400, f"a case has at most {diagrams.MAX_PER_CASE} diagrams")
    did = conn.execute("insert into diagrams(case_id, raw, created_by) values (%s, %s, %s) returning id",
                       (cid, raw, user.sub)).fetchone()[0]
    case.diagrams.append(Diagram(id=did, caption=caption, status="pending", image_id=image_id, credit=credit, research_image_id=research_image_id, **(rights or {})))
    save(conn, cid, case)
    conn.execute("insert into jobs(kind, payload) values ('diagram', jsonb_build_object('diagram_id', %s::bigint))", (did,))
    _reopen(conn, cid)


# the documents behind a case: its own, or its members' when merged. Params: (case id, case id)
DOCS_OF = "(select document_id from cases where id = %s union select document_id from cases where merged_into = %s)"
# a candidate is shown only while its document is live and open to the user
LIVE_DOC = "md.id = i.document_id and md.deleted_at is null and md.acl_groups && %s::text[]"


def candidates_of(conn, cid, user, case):
    """[(image id, width, height, hint)] found in the case's documents, not yet used or dismissed."""
    gone = {*case.dismissed_images, *(d.image_id for d in case.diagrams if d.image_id is not None)}
    rows = conn.execute(f"select i.id, i.width, i.height, i.hint from document_images i join documents md on {LIVE_DOC} "
                        f"where i.document_id in {DOCS_OF} order by i.document_id, i.id", (list(user.groups), cid, cid)).fetchall()
    return [r for r in rows if r[0] not in gone]


def _candidate_png(conn, cid, iid, user, case):
    """The stored PNG of an unused, undismissed candidate of this case, else 404 (never says which part failed)."""
    row = conn.execute(f"select i.png from document_images i join documents md on {LIVE_DOC} "
                       f"where i.id = %s and i.document_id in {DOCS_OF}", (list(user.groups), iid, cid, cid)).fetchone()
    if not row or iid in case.dismissed_images or any(d.image_id == iid for d in case.diagrams):
        raise HTTPException(404, "no such image")
    return bytes(row[0])


@router.get("/review/{cid}/images/{iid}.png")
def candidate_png(cid: int, iid: int, user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        row = conn.execute(
            f"select i.png, c.data from document_images i join documents md on {LIVE_DOC} join cases c on c.id = %s and {REVIEWABLE} "
            f"where i.id = %s and i.document_id in {DOCS_OF.replace('%s', 'c.id')}",
            (list(user.groups), cid, list(user.groups), iid)).fetchone()
    # a used or dismissed candidate is no longer offered, so its thumbnail is not served either
    if not row or iid in row[1].get("dismissed_images", []) or any(d.get("image_id") == iid for d in row[1].get("diagrams", [])):
        raise HTTPException(404, "no such image")
    return Response(bytes(row[0]), media_type="image/png", headers=PNG_HEADERS)


@router.post("/review/{cid}/images/{iid}/use")
def use_image(cid: int, iid: int, caption: str = Form(), v: str = Form(), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        case, _ = load(conn, cid, user, v)
        png = _candidate_png(conn, cid, iid, user, case)
        _queue_diagram(conn, cid, case, png, _caption(conn, caption), user, image_id=iid)  # decoded again by the worker
    return RedirectResponse(f"/review/{cid}", status_code=303)


@router.post("/review/{cid}/images/{iid}/dismiss")
def dismiss_image(cid: int, iid: int, v: str = Form(), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        case, _ = load(conn, cid, user, v)
        _candidate_png(conn, cid, iid, user, case)
        case.dismissed_images.append(iid)
        save(conn, cid, case)  # no reopen: nothing that reaches an output changed
    return RedirectResponse(f"/review/{cid}", status_code=303)


@router.post("/review/{cid}/diagrams/{did}/caption")
def caption_diagram(cid: int, did: int, caption: str = Form(), v: str = Form(), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        case, _ = load(conn, cid, user, v)
        caption = _caption(conn, caption)
        entry = next((d for d in case.diagrams if d.id == did), None)
        if entry is None:
            raise HTTPException(404, "no such diagram")
        entry.caption = caption
        save(conn, cid, case)
        _reopen(conn, cid)
    return RedirectResponse(f"/review/{cid}", status_code=303)


@router.post("/review/{cid}/diagrams/{did}/remove")
def remove_diagram(cid: int, did: int, v: str = Form(), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        case, _ = load(conn, cid, user, v)
        if not any(d.id == did for d in case.diagrams):
            raise HTTPException(404, "no such diagram")
        gone = next(d.image_id for d in case.diagrams if d.id == did)
        if gone is not None:
            case.dismissed_images.append(gone)  # a removed document image is not offered again
        case.diagrams = [d for d in case.diagrams if d.id != did]
        save(conn, cid, case)
        conn.execute("delete from diagrams where id=%s and case_id=%s", (did, cid))
        _reopen(conn, cid)
    return RedirectResponse(f"/review/{cid}", status_code=303)


# --- the solution architecture (#246): a line format the reviewer edits, checked like the proposal ---

def arch_lines(a: Architecture) -> str:
    names = {c.id: c.name for c in a.components}
    return "\n".join([f"title: {a.title}",
                      *(f"component: {c.name} | {c.role} | {c.zone} | {c.kind} | {c.cite}" for c in a.components),
                      *(f"flow: {names[f.src]} -> {names[f.dst]} | {f.label} | {f.mode} | {f.cite}" for f in a.flows),
                      *(f"callout: {c.text} | {c.outcome + 1}" for c in a.callouts)])


def _complaint(e: ValidationError) -> str:  # field names only: no input echoed back
    return "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors(include_input=False, include_url=False, include_context=False))


def parse_architecture(text: str, case: ReferenceCase) -> tuple[Architecture | None, list[str]]:
    """The architecture a line-format text describes, or the errors by line; the case is not changed."""
    errs, title, comps, flows, notes = [], "Solution architecture", [], [], []
    ids = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        key, _, rest = line.partition(":")
        parts = [x.strip() for x in rest.split("|")]
        try:
            match key.strip().lower():
                case "title":
                    title = rest.strip()
                    Architecture(title=title)
                case "component":
                    if len(parts) != 5:
                        raise ValueError("a component is: name | role | zone | kind | cite")
                    if parts[0] in ids:
                        raise ValueError("two components have this name")
                    c = ArchComponent(id=f"c{len(comps) + 1}", name=parts[0], role=parts[1], zone=parts[2], kind=parts[3], cite=parts[4])
                    comps.append(c)
                    ids[c.name] = c.id
                case "flow":
                    src, arrow, dst = (x.strip() for x in parts[0].partition("->"))
                    if len(parts) != 4 or not arrow:
                        raise ValueError("a flow is: A -> B | label | mode | cite")
                    if src not in ids or dst not in ids:
                        raise ValueError("a flow joins two components listed above it")
                    flows.append(ArchFlow(src=ids[src], dst=ids[dst], label=parts[1], mode=parts[2], cite=parts[3]))
                case "callout":
                    if len(parts) != 2 or not re.fullmatch(r"[0-9]{1,3}", parts[1]):
                        raise ValueError("a callout is: text | outcome number")
                    notes.append(ArchCallout(text=parts[0], outcome=int(parts[1]) - 1))
                case _:
                    raise ValueError("a line starts with title:, component:, flow: or callout:")
        except ValidationError as e:
            errs.append(f"Line {n}: {_complaint(e)}")
        except ValueError as e:
            errs.append(f"Line {n}: {e}")
    for what, items, cap in (("components", comps, 12), ("flows", flows, 14), ("callouts", notes, 3)):
        if len(items) > cap:
            errs.append(f"At most {cap} {what}; there are {len(items)}")
    if not comps and not errs:
        errs.append("Add at least one component, or use Remove")
    if len(errs) > 20:
        errs = [*errs[:20], f"and {len(errs) - 20} more"]
    return (None, errs) if errs else (Architecture(title=title, components=comps, flows=flows, callouts=notes, by_reviewer=True), [])


def _arch_log(conn, cid, action, user):
    conn.execute("insert into architecture_changes(case_id, action, changed_by) values (%s,%s,%s)", (cid, action, user.sub))


def _arch_panel(case, form):
    """What the Architecture card shows: the SVG, the flags, the cited points and the editable text."""
    a = case.architecture
    svg = None
    if a and a.components:
        try:
            svg = Markup(archdiagram.to_svg(archdiagram.layout(a), a.title))  # to_svg escapes every string
        except Exception:  # noqa: BLE001 - a drawing failure must not hide the case
            svg = None
    cites = [(f"{f} {i}", p.value, p.source_quote) for f in CITE_POINTS for i, p in enumerate(case.points.get(f, []), 1)
             if p.value and p.source_quote and not p.unsourced]
    cites += [(f"reference {i}", r.value, r.source_quote) for i, r in enumerate(case.tech_references, 1) if r.value and r.source_quote and not r.unsourced]
    text, errors = form if form else (arch_lines(a) if a else "", [])
    note = next((n for n in case.needs_attention if n.lower().startswith("architecture")), None)
    reason = note or ("Extracted before architecture diagrams were added." if case.extraction_version < 4 else "No architecture yet.")
    return dict(a=a, svg=svg, cites=cites, text=text, reason=reason, errors=errors, outcomes=[f"{i}. {o.metric} {o.value}" for i, o in enumerate(case.outcomes, 1) if not o.unsourced])


@router.post("/review/{cid}/architecture")
def save_architecture(cid: int, request: Request, text: str = Form(), v: str = Form(), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        case, _ = load(conn, cid, user, v)
        arch, errs = parse_architecture(text[:20000], case)
        if arch:
            case.architecture = arch
            arch.flags = check_architecture(case)
            writeup.set_arch_note(case)  # the earlier count or failure is no longer true (#264)
            save(conn, cid, case)
            _arch_log(conn, cid, "edit", user)
            _reopen(conn, cid)
    if errs:  # the previous version stays; the page shows what to fix
        r = _detail(cid, request, user, (text, errs))
        r.status_code = 400
        return r
    return RedirectResponse(f"/review/{cid}#architecture", status_code=303)


DATA_CLASSES = ("confidential", "sanitised", "public")  # strictest first


@router.post("/review/{cid}/architecture/regenerate")
def regenerate_architecture(cid: int, v: str = Form(), confirm: bool = Form(False), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:  # no lock is held over the model call below
        case, _ = load(conn, cid, user, v)
        if case.architecture and case.architecture.by_reviewer and not confirm:
            raise HTTPException(400, "confirm that your edited architecture may be replaced")
        classes = {r[0] for r in conn.execute(
            "select s.data_class from cases m join documents d on d.id = m.document_id join sources s on s.id = d.source_id "
            "where m.id = %s or m.merged_into = %s", (cid, cid))}
    work = case.model_copy(deep=True)
    work.architecture = None
    writeup.set_arch_note(work)  # work then holds only what this attempt says (#264)
    writeup.propose(work, next((c for c in DATA_CLASSES if c in classes), "confidential"))
    with db.connect() as conn:
        case, _ = load(conn, cid, user, v)  # 409 if the case changed meanwhile
        if work.architecture:
            case.architecture = work.architecture
        elif case.architecture:
            if not any(n.startswith("architecture proposal failed") for n in work.needs_attention):  # one note per event
                work.needs_attention.append("architecture: no new proposal; the current one is kept")
        else:
            work.needs_attention.append("architecture: the model proposed nothing the points support")
        writeup.set_arch_note(case)  # drops the earlier notes, counts the current diagram's flags (#264)
        case.needs_attention += [n for n in dict.fromkeys(work.needs_attention)  # what this attempt says, once
                                 if n.startswith(("architecture proposal failed", "architecture: ")) and n not in case.needs_attention]
        save(conn, cid, case)
        _arch_log(conn, cid, "regenerate", user)
        _reopen(conn, cid)
    return RedirectResponse(f"/review/{cid}#architecture", status_code=303)


@router.post("/review/{cid}/architecture/remove")
def remove_architecture(cid: int, v: str = Form(), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        case, _ = load(conn, cid, user, v)
        if not case.architecture:
            raise HTTPException(404, "no architecture")
        case.architecture = None
        writeup.set_arch_note(case)
        save(conn, cid, case)
        _arch_log(conn, cid, "remove", user)
        _reopen(conn, cid)
    return RedirectResponse(f"/review/{cid}#architecture", status_code=303)


@router.get("/review/{cid}/architecture.png")
def architecture_png(cid: int, user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        row = conn.execute(f"select c.data from cases c where c.id = %s and {REVIEWABLE}", (cid, list(user.groups))).fetchone()
    a = ReferenceCase.model_validate(row[0]).architecture if row else None
    if not a or not a.components:
        raise HTTPException(404, "no such diagram")
    return Response(archdiagram.to_png(archdiagram.layout(a)), media_type="image/png", headers=PNG_HEADERS)


PNG_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'",
               "Cache-Control": "private, no-store", "Content-Disposition": "inline"}


@router.get("/review/{cid}/diagrams/{did}.png")
def diagram_png(cid: int, did: int, user: User = Depends(require("reviewer"))):
    with db.connect() as conn:  # a ready entry of this case only; raw bytes are never selected
        row = conn.execute(
            f"select d.png from diagrams d join cases c on c.id = d.case_id where d.id = %s and c.id = %s "
            f"and {REVIEWABLE} and d.png is not null and exists (select 1 from jsonb_array_elements(c.data->'diagrams') e "
            f"where (e->>'id')::bigint = d.id and e->>'status' = 'ready')", (did, cid, list(user.groups))).fetchone()
    if not row:
        raise HTTPException(404, "no such diagram")
    return Response(bytes(row[0]), media_type="image/png", headers=PNG_HEADERS)


@router.post("/review/{cid}/approve")
def approve(cid: int, v: str = Form(), diagrams_checked: bool = Form(False), reason: str = Form(""), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        case, text = load(conn, cid, user, v)
        if any(d.status == "pending" for d in case.diagrams):
            raise HTTPException(409, "a diagram is still being checked; reload in a moment")
        if any(PRICE_FLAG in p.flags for p in case.prose.values()):
            raise HTTPException(400, "remove the price or payment term from the write-up")  # whoever wrote it
        if any(p.flags and not p.by_reviewer for p in case.prose.values()):
            raise HTTPException(400, "fix or confirm the flagged write-up")  # editing it counts as confirming
        a = case.architecture
        if a:
            a.flags = check_architecture(case)  # against the evidence as it stands now: an edit may have made the stored flags stale
        if a and any(PRICE_FLAG in f for f in a.flags):
            raise HTTPException(400, "remove the price or payment term from the architecture")
        if a and a.flags and not a.by_reviewer:
            raise HTTPException(400, "fix or confirm the flagged architecture")  # editing it counts as confirming, like the write-up
        if any(d.status == "ready" for d in case.diagrams) and not diagrams_checked:
            raise HTTPException(400, "confirm that you have checked every diagram")
        if isinstance(text, dict) and conn.execute(
                "select count(*) from cases where merged_into = %s and (status = 'rejected' or basis <> 'engagement')", (cid,)).fetchone()[0]:
            raise HTTPException(409, "a member contract is no longer an engagement: un-merge")
        check(case, text)
        for n in SCALARS:  # approve without the unsourced fields
            s = getattr(case, n)
            if s.unsourced:
                s.value, s.source_quote, s.unsourced = None, "", False
        case.capabilities = [c for c in case.capabilities if not c.unsourced]
        case.tech_stack = [t for t in case.tech_stack if not t.unsourced]
        case.tech_references = [t for t in case.tech_references if not t.unsourced]
        case.points = {f: [p for p in ps if not p.unsourced] for f, ps in case.points.items()}
        case.outcomes = [] if case.basis != "delivered" else [o for o in case.outcomes if not o.unsourced]  # contracted scope and offerings claim no results (#139, #213)
        if case.basis != "delivered":
            case.lessons_learned = Sourced[str]()  # nor lessons (#198)
        if case.basis == "capability" and (bad := result_fields(case)):  # whoever wrote it, reviewer included: an offering is not delivery evidence
            raise HTTPException(400, f"remove result language from {', '.join(bad)}: capability documents describe an offering")
        if case.period.unsourced:
            case.period = type(case.period)()
        items = checklist(case)  # after the drops: only what stays counts
        below = not meets_minimum(items)
        if below and not reason.strip():
            raise HTTPException(400, "below the minimum: complete the case or give a reason")
        if not case.title.value:
            raise HTTPException(400, "cannot approve: the title is empty or unsourced")
        case.needs_attention = []
        if not case.summary_sourced():
            case.summary = ""
        save(conn, cid, case)
        # link the case to its registry client, so outputs use its curated label and referenceability
        client_id = anonymise.resolve(case.client_mention.value, anonymise.load_clients(conn))
        try:
            conn.execute("update cases set status='approved', approved_by=%s, approved_at=now(), client_id=%s, "
                         "review_due=now() + interval '12 months' where id=%s", (user.sub, client_id, cid))
        except ForeignKeyViolation:  # the client was deleted after we read the registry (#84): roll back, ask to reload
            raise HTTPException(409, "client changed; reload the page") from None
        if below:
            ok, total = score(items)
            conn.execute("insert into case_approval_overrides(case_id, reason, score, approved_by) values (%s,%s,%s,%s)",
                         (cid, reason.strip()[:500], f"{ok}/{total}", user.sub))
    return RedirectResponse("/review", status_code=303)


@router.post("/review/{cid}/reject")
def reject(cid: int, v: str = Form(), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        if isinstance(load(conn, cid, user, v)[1], dict):
            raise HTTPException(400, "un-merge instead")
        conn.execute("update cases set status='rejected', approved_by=null, approved_at=null, "
                     "review_due=null where id=%s", (cid,))
    return RedirectResponse("/review", status_code=303)


# ---- public sources a reviewer attaches to a case (#199) ----

MAX_SOURCES = 20
SOURCE_KEYS = ("quote", "publisher", "url", "title", "retrieved_at", "type")


def _add_sources(old: list, new: list) -> list:
    """old then new, deduplicated by (url, quote); the caller checks the cap."""
    seen, out = set(), []
    for c in [*old, *new]:
        if (k := (c.get("url"), c.get("quote"))) not in seen:
            seen.add(k)
            out.append(c)
    return out


def _lock_sources(conn, cid, user, s):
    r = conn.execute("select md5(c.public_sources::text), c.public_sources from cases c "
                     "left join documents d on d.id = c.document_id "
                     f"where c.id = %s and {REVIEWABLE} for update of c", (cid, list(user.groups))).fetchone()
    if not r:
        raise HTTPException(404, "no such case")
    if r[0] != s:
        raise HTTPException(409, "case changed; reload the page")
    return r[1]


def reviewable_cases(conn, user, first=None, limit=50):
    """[(id, title, md5 of its attached list)] of the newest cases this reviewer may review, `first` (if any) leading."""
    return conn.execute("select c.id, c.data->'title'->>'value', md5(c.public_sources::text) from cases c "
                        f"left join documents d on d.id = c.document_id where {REVIEWABLE} "
                        "order by c.id = %s desc, c.id desc limit %s", (list(user.groups), first or 0, limit)).fetchall()


async def image_rights(request: Request) -> list[dict]:
    """The ticked images of the attach form: img = ids; rights_ok_<id>, basis_<id>, no_logo_<id> per image."""
    f = await request.form()
    ids = dict.fromkeys(int(i) for i in f.getlist("img") if isinstance(i, str) and i.isascii() and i.isdigit() and len(i) <= 18)
    return [{"id": i, "ok": f.get(f"rights_ok_{i}") == "on", "no_logo": f.get(f"no_logo_{i}") == "on",
             "basis": str(f.get(f"basis_{i}") or "").strip()} for i in ids]


def _attach_images(conn, cid, case, research_id, imgs, user):
    """Queue each ticked image of the user's own research as a pending case diagram, after the reviewer confirmed its rights."""
    from app import research  # here: research imports this module
    if research_id is None:
        raise HTTPException(400, "images come from research")
    clients = anonymise.load_clients(conn)
    for im in imgs:
        if not (im["ok"] and im["no_logo"] and 1 <= len(im["basis"]) <= 200):
            raise HTTPException(400, "confirm the rights, the basis (at most 200 characters) and that no client name or logo shows")
        if any(d.research_image_id == im["id"] for d in case.diagrams):
            continue  # already on this case
        row = conn.execute("select i.id, i.page_url, i.image_url, i.alt, i.png, r.results from research_images i "
                           "join research r on r.id = i.research_id where i.id = %s and i.research_id = %s "
                           "and r.created_by = %s and r.status = 'done'", (im["id"], research_id, user.sub)).fetchone()
        if not row or not research.visible_images([row[:4]], clients):  # not the user's, or hidden on the research page
            raise HTTPException(404, "no such image")
        page_url, alt = row[1], research.visible_images([row[:4]], clients)[0]["alt"]
        src = next((p for p in row[5].get("pages", []) if p.get("url") == page_url), {})
        host = httpx.URL(page_url).host
        if anonymise.blocked(f"{host}\n{page_url}", clients):  # a url is never rewritten
            raise HTTPException(400, "the credit names a protected client")
        title = anonymise.apply(plain(src.get("title") or "") or host, clients)
        credit = f"Image: {title}, {host}, {page_url}, retrieved {str(src.get('retrieved_at') or '')[:10]}"
        if anonymise.blocked(credit, clients):
            raise HTTPException(400, "the credit names a protected client")
        caption = _caption(conn, alt or f"Image from {host}")
        rights = {"rights_by": user.sub, "rights_at": datetime.now(timezone.utc).isoformat(), "rights_basis": im["basis"]}
        _queue_diagram(conn, cid, case, bytes(row[4]), caption, user, credit=credit, research_image_id=im["id"], rights=rights)  # the reviewer is recorded, never credited


def _attach(cid, s, research_id, library_id, n, user, imgs=()):
    """Attach picked quotes from the user's own finished research, or from a library item, to a case they may review."""
    from app import research  # here: research imports this module
    if (research_id is None) == (library_id is None):
        raise HTTPException(400, "attach either research quotes or a library item")
    with db.connect() as conn:
        old = _lock_sources(conn, cid, user, s)
        if research_id is not None:
            kept, ref = (research.picked_claims(conn, research_id, n, user, verbatim=True) if n or not imgs else []), {"research_id": research_id}
        else:
            row = conn.execute("select body from library_items where id=%s", (library_id,)).fetchone()
            if not row:
                raise HTTPException(404, "no such library item")
            stored = row[0]["quotes"]
            picked = [{**stored[i], "n": i} for i in dict.fromkeys(n) if 0 <= i < len(stored)]  # from the row, never the form
            # the item is shared: copy what is visible now (apply() done, blocked ones left out), so no protected name is stored
            kept = research.visible_claims(picked, anonymise.load_clients(conn))[0]
            if not kept:
                raise HTTPException(400, "pick at least one statement")
            ref = {"library_id": library_id}
        # the stored claim is copied verbatim and anonymised again at render
        now = datetime.now(timezone.utc).isoformat()
        new = [{**{k: c.get(k) for k in SOURCE_KEYS}, **ref, "attached_by": user.sub, "attached_at": now} for c in kept]
        merged = _add_sources(old, new)
        if len(merged) > MAX_SOURCES:
            raise HTTPException(400, f"at most {MAX_SOURCES} public sources per case")
        conn.execute("update cases set public_sources=%s where id=%s", (Jsonb(merged), cid))
        if imgs:  # the case row is locked by _lock_sources
            _attach_images(conn, cid, ReferenceCase.model_validate(conn.execute("select data from cases where id=%s", (cid,)).fetchone()[0]),
                           research_id, imgs, user)
    return RedirectResponse(f"/review/{cid}", status_code=303)


@router.post("/review/{cid}/sources")
def attach_sources(cid: int, research_id: int | None = Form(None), library_id: int | None = Form(None),
                   n: list[int] = Form([]), s: str = Form(), user: User = Depends(require("reviewer")),
                   imgs: list[dict] = Depends(image_rights)):
    return _attach(cid, s, research_id, library_id, n, user, imgs)


@router.post("/sources/attach")
def attach_to_chosen_case(target: str = Form(""), research_id: int | None = Form(None), library_id: int | None = Form(None),
                          n: list[int] = Form([]), user: User = Depends(require("reviewer")),
                          imgs: list[dict] = Depends(image_rights)):
    """The research and library pages pick the case: target is "<case id>:<version of its attached list>"."""
    cid, _, s = target.partition(":")
    if not (cid.isascii() and cid.isdigit() and len(cid) <= 18):  # str.isdigit() accepts "²"; a bigint holds 18 digits
        raise HTTPException(400, "choose a case")
    return _attach(int(cid), s, research_id, library_id, n, user, imgs)


@router.post("/review/{cid}/sources/remove")
def remove_source(cid: int, i: int = Form(), s: str = Form(), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        old = _lock_sources(conn, cid, user, s)
        if not 0 <= i < len(old):
            raise HTTPException(400, "no such source")
        conn.execute("update cases set public_sources=%s where id=%s", (Jsonb(old[:i] + old[i + 1:]), cid))
    return RedirectResponse(f"/review/{cid}", status_code=303)


# ---- merging related engagements into one reference (#55) ----

MERGE_FIELDS = (*(f for f in SCALARS if f != "lessons_learned"), "period")  # a merged case is an engagement (#198)


def first_sourced(members, f):
    """The first (cid, case) whose copy of f has a value and a quote, else the first: a merge's default (#140)."""
    def good(o):
        return not o.unsourced and ((o.start or o.end) if f == "period" else o.value not in (None, ""))
    return next((cid for cid, c in members if good(getattr(c, f))), members[0][0])


def combine(members, pick):
    """One case from checked members [(case_id, document_id, ReferenceCase)]; pick maps field -> case_id.

    No model is called. Scalars, duration, team size and period are each taken whole from one member
    (the first sourced member unless picked); capabilities and tech are the de-duplicated union; outcomes are dropped.
    """
    def stamped(item, doc_id):
        item = copy.deepcopy(item)
        if not getattr(item, "by_reviewer", False):  # reviewer text has no origin document (#198)
            item.document_id = doc_id
        return item

    by_id = {cid: (doc, case) for cid, doc, case in members}
    out = {}
    for f in MERGE_FIELDS:
        doc, case = by_id[pick.get(f, first_sourced([(cid, c) for cid, _, c in members], f))]
        out[f] = stamped(getattr(case, f), doc)
    for f in ("capabilities", "tech_stack", "tech_references"):
        seen, items = {}, []  # casefolded value -> index in items
        for _, doc, case in members:
            for i in getattr(case, f):
                k = (i.value or "").casefold()
                if k not in seen:
                    seen[k] = len(items)
                    items.append(stamped(i, doc))
                elif items[seen[k]].unsourced and not i.unsourced:
                    items[seen[k]] = stamped(i, doc)  # same position, the sourced copy's origin (#140)
        out[f] = items
    points = {}
    for _, doc, case in members:
        for f, ps in case.points.items():
            items = points.setdefault(f, [])
            seen = {_norm(i.value or ""): n for n, i in enumerate(items)}
            for p in ps:
                k = _norm(p.value or "")
                if k not in seen:
                    seen[k] = len(items)
                    items.append(stamped(p, doc))
                elif items[seen[k]].unsourced and not p.unsourced:
                    items[seen[k]] = stamped(p, doc)  # the sourced copy and its origin, as for the lists (#140)
    uniq = lambda xs: list(dict.fromkeys(x for x in xs if x))
    notes = [f"merged from {len(members)} contracts: write a summary", "merged: write the case-study text for each section",
             *uniq(n for _, _, c in members for n in c.needs_attention)]
    return ReferenceCase(
        **out, points=points, outcomes=[], summary="", needs_attention=notes, basis="engagement",
        organisations=uniq(o for _, _, c in members for o in c.organisations),
        basis_reason="merged: " + "; ".join(uniq(c.basis_reason for _, _, c in members)))


def candidates(conn, cid, user):
    """Other visible, open engagements for the same registered client as case `cid`: (id, title, document, period, version)."""
    registry = anonymise.load_clients(conn)
    rows = conn.execute(
        f"select c.id, c.data, d.title, {VERSION} from cases c join documents d on d.id = c.document_id "
        f"where {VISIBLE} and c.basis = 'engagement' and c.status <> 'rejected'",
        (list(user.groups),)).fetchall()
    mine = next((d["client_mention"]["value"] for i, d, *_ in rows if i == cid), None)
    want = anonymise.resolve(mine, registry)
    return [(i, d["title"]["value"], t, d["period"], v) for i, d, t, v in rows
            if want is not None and i != cid and anonymise.resolve(d["client_mention"]["value"], registry) == want]


def same_client(conn, cases: dict[int, ReferenceCase]) -> None:
    registry = anonymise.load_clients(conn)
    clients = {anonymise.resolve(c.client_mention.value, registry) for c in cases.values()}
    if len(clients) != 1 or None in clients:
        raise HTTPException(400, "the contracts must be for the same registered client")


def lock_members(conn, user, members):
    """Parse "<case id>:<version>" values, lock and check the cases; return [(id, document id, case, title, text)]."""
    try:
        want = dict((int(i), v) for i, _, v in (m.partition(":") for m in members))
    except ValueError:
        raise HTTPException(400, "malformed member")
    if len(want) < 2:
        raise HTTPException(400, "choose at least 2 contracts to merge")
    sql = ("select c.id, c.data, c.status, c.basis, c.document_id, {v}, d.title, d.text from cases c "
           "left join documents d on d.id = c.document_id where c.id = any(%s) and {vis}")
    got = {r[0]: r for r in conn.execute(
        sql.format(v=VERSION, vis=VISIBLE) + " order by c.id for update of c", (list(want), list(user.groups)))}
    if len(got) != len(want):
        # a member already merged is hidden by VISIBLE; tell it apart from one the user cannot see
        probe = conn.execute(sql.format(v=VERSION, vis=VISIBLE.replace("c.merged_into is null and ", "")),
                             (list(want), list(user.groups))).fetchall()
        raise HTTPException(409 if len(probe) == len(want) else 404,
                            "a contract is already merged" if len(probe) == len(want) else "no such case")
    if any(got[i][5] != v for i, v in want.items()):
        raise HTTPException(409, "case changed; reload the page")
    if any(r[3] != "engagement" or r[4] is None or r[2] == "rejected" for r in got.values()):
        raise HTTPException(400, "only engagements that are not rejected can be merged")
    cases = {i: ReferenceCase.model_validate(got[i][1]) for i in want}
    same_client(conn, cases)
    return [(i, got[i][4], cases[i], got[i][6], got[i][7][:MAX_CHARS]) for i in want]


@router.post("/review/merge/preview")
def merge_preview(request: Request, members: list[str] = Form([]), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        found = lock_members(conn, user, members)
    def show(case, f):
        o = getattr(case, f)
        return (f"{o.start or ''} - {o.end or ''}" if f == "period" else o.value), o.source_quote
    fields = []
    for f in MERGE_FIELDS:
        opts = [(i, *show(c, f)) for i, _, c, _, _ in found]
        if len({o[1] for o in opts}) > 1:  # only the fields where the members differ
            fields.append(dict(field=f, label=f.replace("_", " "), options=opts,
                               default=first_sourced([(i, c) for i, _, c, _, _ in found], f)))
    return page(request, "review_merge.html", user, members=members, titles=[t for *_, t, _ in found], fields=fields)


@router.post("/review/merge")
async def merge(request: Request, members: list[str] = Form([]), user: User = Depends(require("reviewer"))):
    form = await request.form()
    pick = {k[5:]: v for k, v in form.items() if k.startswith("pick_")}
    return await run_in_threadpool(do_merge, members, pick, user)


def do_merge(members, pick, user):
    with db.connect() as conn:
        found = lock_members(conn, user, members)
        ids = [i for i, *_ in found]
        try:
            pick = {f: int(c) for f, c in pick.items() if f in MERGE_FIELDS}
        except ValueError:
            raise HTTPException(400, "invalid choice")
        if any(c not in ids for c in pick.values()):
            raise HTTPException(400, "a choice is not one of the contracts")
        case = combine([(i, d, c) for i, d, c, _, _ in found], pick)
        check(case, {d: t for _, d, _, _, t in found})
        sources = _add_sources([], [c for (m,) in conn.execute(
            "select public_sources from cases where id = any(%s) order by id", (ids,)).fetchall() for c in m])[:MAX_SOURCES]
        new = conn.execute(
            "insert into cases(document_id, member_count, basis, status, data, summary, search_text, public_sources) "
            "values (null, %s, 'engagement', 'extracted', %s, %s, %s, %s) returning id",
            (len(ids), Jsonb(case.model_dump()), case.summary, case.search_text(), Jsonb(sources))).fetchone()[0]
        conn.execute("update cases set merged_into = %s, merged_version = md5(data::text) where id = any(%s)", (new, ids))
    return RedirectResponse(f"/review/{new}", status_code=303)


@router.post("/review/{cid}/unmerge")
def unmerge(cid: int, v: str = Form(), user: User = Depends(require("reviewer"))):
    """Any status, even with a withdrawn member. The merged row is kept: generations and research point at it."""
    with db.connect() as conn:
        # members before M, the order ingest and the untick take via reopen_merged, or they deadlock (#144)
        conn.execute("select 1 from cases where merged_into = %s order by id for update", (cid,))
        row = conn.execute(
            f"select {VERSION} from cases c where c.id = %s and {REVIEWABLE} and c.document_id is null for update of c",
            (cid, list(user.groups))).fetchone()
        if not row:
            raise HTTPException(404, "no such case")
        if row[0] != v:
            raise HTTPException(409, "case changed; reload the page")
        conn.execute("update cases set merged_into = null, status = case when status = 'rejected' then 'rejected' "
                     "else 'extracted' end, approved_by = null, approved_at = null, review_due = null where merged_into = %s", (cid,))
        conn.execute("update cases set status = 'rejected', approved_by = null, approved_at = null, review_due = null "
                     "where id = %s", (cid,))
    return RedirectResponse("/review", status_code=303)


@router.post("/review/{cid}/remerge")
def remerge(cid: int, v: str = Form(), user: User = Depends(require("reviewer"))):
    """Rebuild a merged case from its members' current data, keeping the field choices (#190)."""
    with db.connect() as conn:
        # members before M, as unmerge does (#144)
        conn.execute("select 1 from cases where merged_into = %s order by id for update", (cid,))
        row = conn.execute(
            f"select {VERSION}, c.data from cases c where c.id = %s and {REVIEWABLE} and c.document_id is null "
            "for update of c", (cid, list(user.groups))).fetchone()
        if not row:
            raise HTTPException(404, "no such case")
        if row[0] != v:
            raise HTTPException(409, "case changed; reload the page")
        merged = ReferenceCase.model_validate(row[1])
        found = conn.execute(
            "select m.id, m.document_id, m.data, m.status, m.basis, d.text, d.deleted_at is not null "
            "from cases m join documents d on d.id = m.document_id where m.merged_into = %s order by m.id",
            (cid,)).fetchall()
        live = [m for m in found if not m[6]]
        if any(m[3] == "rejected" or m[4] != "engagement" for m in live):
            raise HTTPException(409, "a member contract is no longer an engagement: un-merge")
        if len(live) < 2:
            raise HTTPException(409, "fewer than 2 contracts remain: un-merge")
        cases = {m[0]: ReferenceCase.model_validate(m[2]) for m in live}
        same_client(conn, cases)
        pick = {f: m[0] for f in MERGE_FIELDS for m in live if m[1] == getattr(merged, f).document_id}
        case = combine([(m[0], m[1], cases[m[0]]) for m in live], pick)
        check(case, {m[1]: m[5][:MAX_CHARS] for m in live})
        case.diagrams, case.dismissed_images = merged.diagrams, merged.dismissed_images  # the merged case's own; combine() starts empty
        save(conn, cid, case)
        conn.execute("update cases set status = 'extracted', approved_by = null, approved_at = null, "
                     "review_due = null, member_count = %s where id = %s", (len(live), cid))
        conn.execute("update cases set merged_into = null where id = any(%s)", ([m[0] for m in found if m[6]],))
        conn.execute("update cases set merged_version = md5(data::text) where id = any(%s)", ([m[0] for m in live],))
    return RedirectResponse(f"/review/{cid}", status_code=303)
