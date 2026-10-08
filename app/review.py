from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app import anonymise, db
from app.extract import MAX_CHARS, check
from app.main import User, require
from app.schema import Outcome, ReferenceCase, Sourced

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")  # autoescape on

SCALARS = ("title", "client_mention", "industry", "region", "engagement_type", "challenge",
           "solution", "duration_months", "team_size")
INTS = ("duration_months", "team_size")
LISTS = ("capabilities", "tech_stack", "outcomes")
# a case can be edited/approved/rejected only while it is new or its approval has expired
OPEN = "(c.status = 'extracted' or (c.status = 'approved' and c.review_due < now()))"
# need-to-know: a reviewer only sees cases from live documents they can open at the source
ACL = "d.deleted_at is null and d.acl_groups && %s::text[]"
# the version a reviewer saw; a re-extraction in between must not be approved unseen
VERSION = "md5(c.data::text)"


def page(request, name, user, **ctx):
    return templates.TemplateResponse(request, name, {"user": user, **ctx})


def load(conn, cid, user, version):
    """Lock the case row and return (case, document text).

    404 if the reviewer cannot see it (never confirm it exists), 409 if it changed since the
    page was loaded or someone else already decided it.
    """
    groups = list(user.groups)
    row = conn.execute(
        f"select c.data, d.text from cases c join documents d on d.id = c.document_id "
        f"where c.id = %s and {ACL} and {OPEN} and {VERSION} = %s for update of c",
        (cid, groups, version)).fetchone()
    if not row:
        seen = conn.execute(
            f"select {VERSION} = %s from cases c join documents d on d.id = c.document_id "
            f"where c.id = %s and {ACL}", (version, cid, groups)).fetchone()
        if not seen:
            raise HTTPException(404, "no such case")
        raise HTTPException(409, "case is not open for review" if seen[0] else "case changed; reload the page")
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


def rows(case):
    def row(path, label, obj, names):
        return dict(path=path, label=label, quote=obj.source_quote, unsourced=obj.unsourced,
                    inputs=[(n, "" if getattr(obj, n) is None else getattr(obj, n)) for n in names])
    out = [row(n, n.replace("_", " "), getattr(case, n), ["value"]) for n in SCALARS]
    for n in ("capabilities", "tech_stack"):
        out += [row(f"{n}.{i}", f"{n.replace('_', ' ')} #{i + 1}", o, ["value"]) for i, o in enumerate(getattr(case, n))]
    out += [row(f"outcomes.{i}", f"outcome #{i + 1}", o, ["metric", "value"]) for i, o in enumerate(case.outcomes)]
    out.append(row("period", "period", case.period, ["start", "end"]))
    out.append(dict(path="summary", label="summary", quote="", unsourced=False, no_quote=True,
                    inputs=[("value", case.summary)]))
    return out


@router.get("/")
def landing(request: Request, user: User = Depends(require("user"))):
    return page(request, "landing.html", user)


@router.get("/review")
def review_list(request: Request, user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        found = conn.execute(
            f"select c.id, c.status, c.data, d.title from cases c join documents d on d.id = c.document_id "
            f"where {ACL} and {OPEN} order by c.id", (list(user.groups),)).fetchall()
        # reminders: approvals that expire within 30 days (the list above only has the expired ones)
        soon = conn.execute(
            "select c.id, c.data, d.title, c.review_due from cases c join documents d on d.id = c.document_id "
            f"where {ACL} and c.status = 'approved' and c.review_due > now() "
            "and c.review_due <= now() + interval '30 days' order by c.review_due", (list(user.groups),)).fetchall()
    cases = [dict(id=i, status=s, title=d["title"]["value"], document=t,
                  attention=len(d.get("needs_attention", [])), unsourced=count_unsourced(d))
             for i, s, d, t in found]
    due_soon = [dict(id=i, title=d["title"]["value"], document=t, due=due.strftime("%Y-%m-%d")) for i, d, t, due in soon]
    return page(request, "review_list.html", user, cases=cases, due_soon=due_soon)


@router.get("/review/{cid}")
def review_detail(cid: int, request: Request, user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        r = conn.execute(
            f"select c.data, c.status, d.title, d.external_id, s.name, {OPEN}, {VERSION} "
            f"from cases c join documents d on d.id = c.document_id join sources s on s.id = d.source_id "
            f"where c.id = %s and {ACL}", (cid, list(user.groups))).fetchone()
        registry = anonymise.load_clients(conn)
    if not r:
        raise HTTPException(404, "no such case")
    case = ReferenceCase.model_validate(r[0])  # the document text is deliberately not shown
    notes = list(case.needs_attention)
    if r[5]:  # organisations come from extraction; no LLM call on page view
        notes += [f"organisation not in client registry: {o}"
                  for o in anonymise.unlisted([*case.organisations, case.client_mention.value or ""], registry)]
    return page(request, "review_detail.html", user, id=cid, rows=rows(case), notes=notes,
                basis=case.basis, basis_reason=case.basis_reason, status=r[1], document=r[2],
                external_id=r[3], source=r[4], reviewable=r[5], v=r[6])


@router.post("/review/{cid}/edit")
def edit(cid: int, field: str = Form(), value: str | None = Form(None), metric: str | None = Form(None),
         start: str | None = Form(None), end: str | None = Form(None), quote: str | None = Form(None),
         v: str = Form(), user: User = Depends(require("reviewer"))):
    # `unsourced` is never read from the form: check() recomputes it against the document
    with db.connect() as conn:
        case, text = load(conn, cid, user, v)
        name, _, idx = field.partition(".")
        try:
            if field == "summary":
                case.summary = value or ""
                obj = None
            elif name in SCALARS and not idx:
                obj = getattr(case, name)
                if value is not None:
                    obj.value = (int(value) if value.strip() else None) if name in INTS else (value or None)
            elif name in LISTS and idx:
                obj = getattr(case, name)[int(idx)]
                if isinstance(obj, Outcome):
                    obj.metric, obj.value = metric or "", value or ""
                elif value is not None:
                    obj.value = value or None
            elif field == "period":
                obj = case.period
                obj.start, obj.end = start or None, end or None
            else:
                raise HTTPException(400, "unknown field")
            if obj is not None and quote is not None:
                obj.source_quote = quote
            case = ReferenceCase.model_validate(case.model_dump())  # re-run validators (summary length)
        except (ValueError, IndexError, ValidationError):  # no input echoed back
            raise HTTPException(400, "invalid value")
        check(case, text)
        fix_summary(case)
        save(conn, cid, case)
    return RedirectResponse(f"/review/{cid}", status_code=303)


@router.post("/review/{cid}/approve")
def approve(cid: int, v: str = Form(), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        case, text = load(conn, cid, user, v)
        check(case, text)
        for n in SCALARS:  # approve without the unsourced fields
            s = getattr(case, n)
            if s.unsourced:
                s.value, s.source_quote, s.unsourced = None, "", False
        case.capabilities = [c for c in case.capabilities if not c.unsourced]
        case.tech_stack = [t for t in case.tech_stack if not t.unsourced]
        case.outcomes = [o for o in case.outcomes if not o.unsourced]
        if case.period.unsourced:
            case.period = type(case.period)()
        if not case.title.value:
            raise HTTPException(400, "cannot approve: the title is empty or unsourced")
        case.needs_attention = []
        if not case.summary_sourced():
            case.summary = ""
        save(conn, cid, case)
        # link the case to its registry client, so outputs use its curated label and referenceability
        client_id = anonymise.resolve(case.client_mention.value, anonymise.load_clients(conn))
        conn.execute("update cases set status='approved', approved_by=%s, approved_at=now(), client_id=%s, "
                     "review_due=now() + interval '12 months' where id=%s", (user.sub, client_id, cid))
    return RedirectResponse("/review", status_code=303)


@router.post("/review/{cid}/reject")
def reject(cid: int, v: str = Form(), user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        load(conn, cid, user, v)
        conn.execute("update cases set status='rejected', approved_by=null, approved_at=null, "
                     "review_due=null where id=%s", (cid,))
    return RedirectResponse("/review", status_code=303)
