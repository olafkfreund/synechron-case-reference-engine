"""The shared knowledge library (#214): research quotes and cited briefs saved for reuse by every portal user.

An item copies its quotes and brief text, so it does not depend on the research row. Its text is never part of
cases.search_text: the library has its own search. Every view and download runs apply() and blocked() again.
"""
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from psycopg.types.json import Jsonb

from app import anonymise, brief, db, render, research
from app.main import User, require
from app.review import SOURCE_KEYS, page
from app.schema import quote_in

router = APIRouter()
STALE_DAYS = 365
LIST_LIMIT = 200
WITHHELD = "[withheld]"


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _item(conn, lid):
    row = conn.execute(
        "select topic, kind, query, body, created_by, floor(extract(epoch from now() - created_at) / 86400)::int, "
        f"floor(extract(epoch from now() - checked_at) / 86400)::int, checked_at < now() - interval '{STALE_DAYS} days' "
        "from library_items where id=%s", (lid,)).fetchone()
    if not row:
        raise HTTPException(404, "no such library item")
    return row


@router.post("/library/save")
def save(research_id: int | None = Form(None), brief_id: int | None = Form(None), n: list[int] = Form([]),
         topic: str = Form(), user: User = Depends(require("user"))):
    if (research_id is None) == (brief_id is None):
        raise HTTPException(400, "save either research quotes or a brief")
    with db.connect() as conn:
        clients = anonymise.load_clients(conn)
        try:
            t = research.finalize(topic.strip(), clients)  # scrubbed like a research query
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if research_id is not None:
            claims = research.picked_claims(conn, research_id, n, user, strict=True)  # stored row, by index
            rid, kind, body = research_id, "quotes", {"quotes": [{k: c.get(k) for k in SOURCE_KEYS} for c in claims]}
        else:
            status, _, _, quotes, b = brief.owned(conn, brief_id, user)
            if status != "done" or b.get("flags"):
                raise HTTPException(409, "only a finished brief with no flagged sentences can be saved")
            rid = conn.execute("select research_id from briefs where id=%s", (brief_id,)).fetchone()[0]
            kind, body = "brief", {"quotes": quotes, "brief": {k: b[k] for k in ("title", "takeaways", "themes")}}
        query = (conn.execute("select query from research where id=%s", (rid,)).fetchone() or [""])[0] if rid else ""
        if anonymise.blocked(json.dumps([t, query, body], ensure_ascii=False), clients):  # topic, quotes, brief text, URLs
            raise HTTPException(400, "the item names a protected client; it cannot be saved")
        lid = conn.execute("insert into library_items(topic, kind, query, body, created_by) values (%s,%s,%s,%s,%s) returning id",
                           (t, kind, query, Jsonb(body), user.sub)).fetchone()[0]
    return RedirectResponse(f"/library/{lid}", status_code=303)


@router.get("/library")
def index(request: Request, q: str = Query("", max_length=200), user: User = Depends(require("user"))):
    q = q.strip()
    with db.connect() as conn:
        clients = anonymise.load_clients(conn)
        # a search naming a protected client finds nothing: it must not confirm that a name is in the library
        rows = [] if q and anonymise.blocked(q, clients) else conn.execute(
            "select id, topic, kind, jsonb_array_length(body->'quotes'), "
            "floor(extract(epoch from now() - created_at) / 86400)::int, "
            f"checked_at < now() - interval '{STALE_DAYS} days' from library_items "
            "where %s = '' or lower(topic) like lower(%s) or body::text ilike %s order by id desc limit %s",
            (q, _like(q), _like(q), LIST_LIMIT)).fetchall()
    items = [{"id": r[0], "topic": WITHHELD if anonymise.blocked(r[1], clients) else anonymise.apply(r[1], clients),
              "kind": r[2], "sources": r[3], "age": r[4], "stale": r[5]} for r in rows]
    return page(request, "library.html", user, items=items, q=q)


@router.get("/library/{lid}")
def view(lid: int, request: Request, user: User = Depends(require("user"))):
    with db.connect() as conn:
        topic, kind, query, body, creator, age, checked_age, stale = _item(conn, lid)
        clients = anonymise.load_clients(conn)
        rid = body.get("rechecks", {}).get(user.sub)
        again = conn.execute("select status, results from research where id=%s and created_by=%s", (rid, user.sub)).fetchone() if rid else None
    claims, omitted, sec = [], 0, None
    if kind == "quotes":
        claims, omitted = research.visible_claims(body["quotes"], clients)
    else:
        try:
            sec = render.brief_section(body["brief"], body["quotes"], clients)
        except render.Withheld:
            sec = {"note": "This brief is withheld: it names a protected client."}
    found = None
    if again and again[0] == "done":  # by URL and the quote text, on the freshly fetched pages
        pages = again[1].get("pages", [])
        found = [any(p.get("url") == c.get("url") and quote_in(p.get("markdown", ""), c.get("quote", "")) for p in pages)
                 for c in claims]
    shown = WITHHELD if anonymise.blocked(topic, clients) else anonymise.apply(topic, clients)
    return page(request, "library_item.html", user, lid=lid, topic=shown, kind=kind, age=age, checked_age=checked_age, stale=stale,
                claims=claims, omitted=omitted, sec=sec, can_recheck=bool(query), again=again[0] if again else None,
                found=found, can_delete=creator == user.sub or "admin" in user.roles)


@router.post("/library/{lid}/export")
def export(lid: int, request: Request, format: str = Form(), user: User = Depends(require("user"))):
    if format not in ("md", "csv", "copy"):
        raise HTTPException(400, "unknown format")
    with db.connect() as conn:
        _, kind, _, body, *_ = _item(conn, lid)
        if kind != "quotes":
            raise HTTPException(400, "download a brief as Word, PDF or Markdown")
        claims = research.visible_claims(body["quotes"], anonymise.load_clients(conn))[0]
        if not claims:
            raise HTTPException(409, "every quote names a protected client")
        conn.execute("insert into generations(user_id, format) values (%s,%s)", (user.sub, f"library-{format}"))
    if format == "copy":
        return page(request, "research_export.html", user, text=research._export_md(claims), back=f"/library/{lid}")
    return research.export_file(claims, format, f"library-{lid}")


@router.post("/library/{lid}/download")
def download(lid: int, format: str = Form(), user: User = Depends(require("user"))):
    if format not in ("docx", "pdf_docx", "md"):
        raise HTTPException(400, "unknown format")
    with db.connect() as conn:
        _, kind, _, body, *_ = _item(conn, lid)
        if kind != "brief":
            raise HTTPException(400, "export quotes as Markdown or CSV")
        data, mime, ext = brief.make_output(body["brief"], body["quotes"], anonymise.load_clients(conn), format)
        conn.execute("insert into generations(user_id, format) values (%s,%s)", (user.sub, f"library-{format}"))
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="library-{lid}.{ext}"',
                                                    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/library/{lid}/recheck")
def recheck(lid: int, user: User = Depends(require("user"))):
    """Run the stored query again as the user's own research. Nothing in the item changes until they choose to."""
    with db.connect() as conn:
        query = _item(conn, lid)[2]
        try:
            q = research.finalize(query, anonymise.load_clients(conn)) if query else ""  # a client may be protected since
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if not q:
            raise HTTPException(400, "this item has no query to run again")
        rid = conn.execute("insert into research(query, created_by) values (%s,%s) returning id", (q, user.sub)).fetchone()[0]
        conn.execute("insert into jobs(kind, payload) values ('research', jsonb_build_object('research_id', %s::bigint))", (rid,))
        conn.execute("update library_items set body = body || jsonb_build_object('rechecks', "
                     "coalesce(body->'rechecks', '{}'::jsonb) || jsonb_build_object(%s::text, %s::bigint)) where id=%s",
                     (user.sub, rid, lid))
    return RedirectResponse(f"/library/{lid}", status_code=303)


@router.post("/library/{lid}/checked")
def checked(lid: int, user: User = Depends(require("user"))):
    with db.connect() as conn:
        _item(conn, lid)
        conn.execute("update library_items set checked_at = now() where id=%s", (lid,))
    return RedirectResponse(f"/library/{lid}", status_code=303)


@router.post("/library/{lid}/delete")
def delete(lid: int, user: User = Depends(require("user"))):
    with db.connect() as conn:
        creator = _item(conn, lid)[4]
        if creator != user.sub and "admin" not in user.roles:
            raise HTTPException(404, "no such library item")  # never confirm it exists
        conn.execute("delete from library_items where id=%s", (lid,))
    return RedirectResponse("/library", status_code=303)
