import copy

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.templating import Jinja2Templates
from pathlib import Path
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app import anonymise, db
from app.extract import MAX_CHARS, check
from app.main import User, require
from app.schema import Outcome, Period, ReferenceCase, Sourced

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")  # autoescape on

SCALARS = ("title", "client_mention", "industry", "region", "engagement_type", "challenge",
           "solution", "duration_months", "team_size")
INTS = ("duration_months", "team_size")
LISTS = ("capabilities", "tech_stack", "outcomes")
# a case can be edited/approved/rejected only while it is new or its approval has expired
OPEN = "(c.status = 'extracted' or (c.status = 'approved' and c.review_due < now()))"
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
                    inputs=[(n, "" if getattr(obj, n) is None else getattr(obj, n)) for n in names])
    out = [row(n, n.replace("_", " "), getattr(case, n), ["value"]) for n in SCALARS]
    def new(n, label, names):
        return dict(path=f"{n}.new", label=label, quote="", unsourced=False, new=True, inputs=[(i, "") for i in names])
    for n, label in (("capabilities", "capability"), ("tech_stack", "tech")):
        out += [dict(row(f"{n}.{i}", f"{n.replace('_', ' ')} #{i + 1}", o, ["value"]), item=True) for i, o in enumerate(getattr(case, n))]
        out.append(new(n, f"add {label}", ["value"]))
    out += [dict(row(f"outcomes.{i}", f"outcome #{i + 1}", o, ["metric", "value"]), item=True) for i, o in enumerate(case.outcomes)]
    out.append(new("outcomes", "add outcome", ["metric", "value"]))
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
            f"select c.id, c.status, c.data, d.title, c.member_count from cases c left join documents d on d.id = c.document_id "
            f"where {REVIEWABLE} and {OPEN} order by c.id", (list(user.groups),)).fetchall()
        # reminders: approvals that expire within 30 days (the list above only has the expired ones)
        soon = conn.execute(
            "select c.id, c.data, d.title, c.review_due, c.member_count from cases c left join documents d on d.id = c.document_id "
            f"where {REVIEWABLE} and c.status = 'approved' and c.review_due > now() "
            "and c.review_due <= now() + interval '30 days' order by c.review_due", (list(user.groups),)).fetchall()
    cases = [dict(id=i, status=s, title=d["title"]["value"], document=t,
                  attention=len(d.get("needs_attention", [])), unsourced=count_unsourced(d), members=n)
             for i, s, d, t, n in found]
    due_soon = [dict(id=i, title=d["title"]["value"], document=t, due=due.strftime("%Y-%m-%d"), members=n) for i, d, t, due, n in soon]
    return page(request, "review_list.html", user, cases=cases, due_soon=due_soon)


@router.get("/review/{cid}")
def review_detail(cid: int, request: Request, user: User = Depends(require("reviewer"))):
    with db.connect() as conn:
        r = conn.execute(
            f"select c.data, c.status, d.title, d.external_id, s.name, {OPEN}, {VERSION}, c.member_count, c.basis "
            f"from cases c left join documents d on d.id = c.document_id left join sources s on s.id = d.source_id "
            f"where c.id = %s and {REVIEWABLE}", (cid, list(user.groups))).fetchone()
        registry = anonymise.load_clients(conn)
        merged = r and r[7] is not None
        members = conn.execute(
            "select m.id, d.title, s.name, d.external_id, m.data->>'basis_reason', m.status, d.deleted_at is not null, d.id "
            "from cases m join documents d on d.id = m.document_id join sources s on s.id = d.source_id "
            "where m.merged_into = %s order by m.id", (cid,)).fetchall() if merged else []
        cands = candidates(conn, cid, user) if r and r[5] and r[7] is None and r[8] == "engagement" else []
    if not r:
        raise HTTPException(404, "no such case")
    case = ReferenceCase.model_validate(r[0])  # the document text is deliberately not shown
    notes = list(case.needs_attention)
    # organisations come from extraction; no LLM call on page view
    unlisted = anonymise.unlisted([*case.organisations, case.client_mention.value or ""], registry) if r[5] else []
    return page(request, "review_detail.html", user, id=cid, rows=rows(case, {m[7]: m[1] for m in members}), notes=notes,
                unlisted=unlisted, members=members, cands=cands,
                basis=case.basis, basis_reason=case.basis_reason, status=r[1], document=r[2],
                external_id=r[3], source=r[4], merged=bool(members),
                reviewable=r[5] and not any(m[6] for m in members), v=r[6])


@router.post("/review/{cid}/edit")
def edit(cid: int, field: str = Form(), value: str | None = Form(None), metric: str | None = Form(None),
         start: str | None = Form(None), end: str | None = Form(None), quote: str | None = Form(None),
         action: str = Form("save"), v: str = Form(), user: User = Depends(require("reviewer"))):
    # `unsourced` is never read from the form: check() recomputes it against the document
    with db.connect() as conn:
        case, text = load(conn, cid, user, v)
        name, _, idx = field.partition(".")
        try:
            if action not in ("save", "remove"):
                raise HTTPException(400, "unknown action")
            if action == "remove" and not (name in LISTS and idx and idx != "new"):
                raise HTTPException(400, "unknown field")
            if field == "summary":
                case.summary = value or ""
                obj = None
            elif name in SCALARS and not idx:
                obj = getattr(case, name)
                if value is not None:
                    obj.value = (int(value) if value.strip() else None) if name in INTS else (value or None)
            elif name in LISTS and idx == "new":
                if not (value or "").strip() or not (quote or "").strip() or (name == "outcomes" and not (metric or "").strip()):
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
        if isinstance(load(conn, cid, user, v)[1], dict):
            raise HTTPException(400, "un-merge instead")
        conn.execute("update cases set status='rejected', approved_by=null, approved_at=null, "
                     "review_due=null where id=%s", (cid,))
    return RedirectResponse("/review", status_code=303)


# ---- merging related engagements into one reference (#55) ----

MERGE_FIELDS = (*SCALARS, "period")  # a reviewer picks which member each of these comes from


def combine(members, pick):
    """One case from checked members [(case_id, document_id, ReferenceCase)]; pick maps field -> case_id.

    No model is called. Scalars, duration, team size and period are each taken whole from one member
    (the first unless picked); capabilities and tech are the de-duplicated union; outcomes are dropped.
    """
    def stamped(item, doc_id):
        item = copy.deepcopy(item)
        item.document_id = doc_id
        return item

    by_id = {cid: (doc, case) for cid, doc, case in members}
    out = {}
    for f in MERGE_FIELDS:
        doc, case = by_id[pick.get(f, members[0][0])]
        out[f] = stamped(getattr(case, f), doc)
    for f in ("capabilities", "tech_stack"):
        seen, items = set(), []
        for _, doc, case in members:
            for i in getattr(case, f):
                if (k := (i.value or "").casefold()) not in seen:
                    seen.add(k)
                    items.append(stamped(i, doc))
        out[f] = items
    uniq = lambda xs: list(dict.fromkeys(x for x in xs if x))
    notes = [f"merged from {len(members)} contracts: write a summary",
             *uniq(n for _, _, c in members for n in c.needs_attention)]
    return ReferenceCase(
        **out, outcomes=[], summary="", needs_attention=notes, basis="engagement",
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
    registry = anonymise.load_clients(conn)
    cases = {i: ReferenceCase.model_validate(got[i][1]) for i in want}
    clients = {anonymise.resolve(c.client_mention.value, registry) for c in cases.values()}
    if len(clients) != 1 or None in clients:
        raise HTTPException(400, "the contracts must be for the same registered client")
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
            fields.append(dict(field=f, label=f.replace("_", " "), options=opts))
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
        new = conn.execute(
            "insert into cases(document_id, member_count, basis, status, data, summary, search_text) "
            "values (null, %s, 'engagement', 'extracted', %s, %s, %s) returning id",
            (len(ids), Jsonb(case.model_dump()), case.summary, case.search_text())).fetchone()[0]
        conn.execute("update cases set merged_into = %s where id = any(%s)", (new, ids))
    return RedirectResponse(f"/review/{new}", status_code=303)


@router.post("/review/{cid}/unmerge")
def unmerge(cid: int, v: str = Form(), user: User = Depends(require("reviewer"))):
    """Any status, even with a withdrawn member. The merged row is kept: generations and research point at it."""
    with db.connect() as conn:
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
