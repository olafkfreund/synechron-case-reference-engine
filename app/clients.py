import re

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from app import anonymise, db, diagrams
from app.main import User, require
from app.review import PNG_HEADERS, page

router = APIRouter()
# the version an admin saw; a concurrent edit in between returns 409 instead of being overwritten
VERSION = "md5(row(name, aliases, anonymised_label, referenceable, logo_allowed)::text)"


def clean(name, aliases, label, referenceable, cid=None):
    name, label = name.strip(), label.strip()
    aliases = list(dict.fromkeys(a.strip() for a in aliases.splitlines() if a.strip()))
    if not name or not label:
        raise HTTPException(400, "name and label are required")
    if anonymise.has_name(label, [name, *aliases]):
        raise HTTPException(400, "the label must not contain the client's name or an alias")
    # nor another client's protected name: every render for this client would then be blocked
    with db.connect() as conn:
        others = conn.execute("select name, aliases from clients where not referenceable "
                              "and id is distinct from %s", (cid,)).fetchall()
    if anonymise.has_name(label, [n for nm, al in others for n in [nm, *al]]):
        raise HTTPException(400, "the label must not contain another client's name or alias")
    if not referenceable:  # this client's names are protected: no other label may contain them (#115)
        with db.connect() as conn:
            labels = conn.execute("select name, anonymised_label from clients where id is distinct from %s",
                                  (cid,)).fetchall()
        hit = sorted(nm for nm, lbl in labels if anonymise.has_name(lbl, [name, *aliases]))
        if hit:
            raise HTTPException(400, "these clients' labels contain this client's name or an alias; "
                                     f"change them first: {', '.join(hit)}")
    # a name or alias shared with another client would attribute cases to the wrong client
    with db.connect() as conn:
        taken = conn.execute("select name, aliases from clients where id is distinct from %s", (cid,)).fetchall()
    taken_keys = {anonymise._key(n) for nm, al in taken for n in [nm, *al]}
    if any(anonymise._key(n) in taken_keys for n in [name, *aliases]):
        raise HTTPException(400, "a name or alias is already used by another client")
    return name, aliases, label


@router.get("/admin/clients")
def clients_page(request: Request, kept: int = Query(0, ge=0), user: User = Depends(require("admin"))):
    with db.connect() as conn:
        rows = conn.execute(f"select id, name, aliases, anonymised_label, referenceable, logo_allowed, "
                            f"{VERSION}, (select case when png is not null then 'ready' when raw is not null then 'pending' else 'rejected' end "
                            f"from client_logos where client_id = clients.id), "
                            f"(select reason from client_logos where client_id = clients.id) from clients order by name").fetchall()
    return page(request, "clients.html", user, clients=rows, kept=kept)


@router.post("/admin/clients")
def create(name: str = Form(), aliases: str = Form(""), anonymised_label: str = Form(),
           referenceable: bool = Form(False), logo_allowed: bool = Form(False),
           next: str = Form(""), user: User = Depends(require("admin"))):
    name, alias_list, label = clean(name, aliases, anonymised_label, referenceable)
    try:
        with db.connect() as conn:
            conn.execute("insert into clients(name, aliases, anonymised_label, referenceable, logo_allowed) "
                         "values (%s,%s,%s,%s,%s)", (name, alias_list, label, referenceable, logo_allowed))
    except UniqueViolation:
        raise HTTPException(400, "a client with that name already exists") from None
    # only back to a review page: never an open redirect
    return RedirectResponse(next if re.fullmatch(r"/review/[0-9]+", next) else "/admin/clients", status_code=303)


@router.post("/admin/clients/{cid}")
def update(cid: int, name: str = Form(), aliases: str = Form(""), anonymised_label: str = Form(),
           v: str = Form(), referenceable: bool = Form(False), logo_allowed: bool = Form(False),
           user: User = Depends(require("admin"))):
    with db.connect() as conn:  # unknown id is 404 before any validation message
        old = conn.execute(f"select name, aliases, referenceable, {VERSION} from clients where id=%s", (cid,)).fetchone()
        if not old:
            raise HTTPException(404, "no such client")
    if old[3] != v:  # changed since the form loaded: say so before validating names the admin never saw
        raise HTTPException(409, "client changed; reload the page")
    kept = []
    if not old[2] and not referenceable:  # a protected client keeps every name it had (#164)
        have = {anonymise._key(n) for n in [name, *aliases.splitlines()] if n.strip()}
        for n in [old[0], *old[1]]:
            if anonymise._key(n) not in have:
                have.add(anonymise._key(n))
                kept.append(n)
        aliases = "\n".join([aliases, *kept])
    name, alias_list, label = clean(name, aliases, anonymised_label, referenceable, cid)
    try:
        with db.connect() as conn:
            n = conn.execute(
                f"update clients set name=%s, aliases=%s, anonymised_label=%s, referenceable=%s, logo_allowed=%s "
                f"where id=%s and {VERSION}=%s", (name, alias_list, label, referenceable, logo_allowed, cid, v)).rowcount
            if not n:
                exists = conn.execute("select 1 from clients where id=%s", (cid,)).fetchone()
                raise HTTPException(409 if exists else 404, "client changed; reload the page" if exists else "no such client")
    except UniqueViolation:
        raise HTTPException(400, "a client with that name already exists") from None
    return RedirectResponse(f"/admin/clients?kept={len(kept)}" if kept else "/admin/clients", status_code=303)


@router.post("/admin/clients/{cid}/delete")
def delete(cid: int, v: str = Form(), user: User = Depends(require("admin"))):
    # only referenceable, unlinked clients: a protected name must stay hidden, a linked case keeps its label
    try:
        with db.connect() as conn:
            had_logo = conn.execute("select 1 from client_logos where client_id=%s", (cid,)).fetchone()
            n = conn.execute(f"delete from clients cl where id=%s and {VERSION}=%s and referenceable "
                             "and not exists (select 1 from cases where client_id = cl.id)", (cid, v)).rowcount
            if not n:
                row = conn.execute("select referenceable, (select count(*) from cases where client_id=%s) "
                                   "from clients where id=%s", (cid, cid)).fetchone()
                if not row:
                    raise HTTPException(404, "no such client")
                if not row[0]:
                    raise HTTPException(400, "a non-referenceable client cannot be deleted: its names must stay hidden")
                if row[1]:
                    raise HTTPException(400, f"{row[1]} case(s) use this client; it cannot be deleted")
                raise HTTPException(409, "client changed; reload the page")
            if had_logo:  # the logo row goes with the client (cascade): say so in the log
                _logo_audit(conn, cid, "remove (client deleted)", user)
    except ForeignKeyViolation:
        raise HTTPException(400, "a case uses this client; it cannot be deleted") from None
    return RedirectResponse("/admin/clients", status_code=303)


SHOWN = "c.referenceable and c.logo_allowed and l.png is not null"  # the display rule (#267), the only copy


def logo_cids(conn, ids) -> set[int]:
    """Which of these clients show a logo, without reading the images. A name that outputs would mask (fail-closed,
    like search.clean) shows none: the logo would identify the client. logo_for and every surface use this rule."""
    rows = conn.execute(f"select c.id, c.name from client_logos l join clients c on c.id = l.client_id "
                        f"where c.id = any(%s) and {SHOWN}", (list(ids),)).fetchall()
    known = anonymise.load_clients(conn) if rows else []
    return {i for i, name in rows if not anonymise.masks(name, known)}


def logo_for(conn, client_id) -> bytes | None:
    """The decoded logo of a client logo_cids() allows, else None."""
    if client_id not in logo_cids(conn, [client_id]):
        return None
    return bytes(conn.execute("select png from client_logos where client_id=%s", (client_id,)).fetchone()[0])


def _logo_audit(conn, cid, action, user):
    conn.execute("insert into client_logo_changes(client_id, action, changed_by) values (%s,%s,%s)", (cid, action, user.sub))


@router.post("/admin/clients/{cid}/logo")
def upload_logo(cid: int, file: UploadFile, user: User = Depends(require("admin"))):
    raw = file.file.read(diagrams.LOGO_MAX_BYTES + 1)  # the decoding is the worker's job
    if len(raw) > diagrams.LOGO_MAX_BYTES:
        raise HTTPException(413, f"a logo is at most {diagrams.LOGO_MAX_BYTES // 1024} KiB")
    if not raw.startswith(diagrams.MAGIC):
        raise HTTPException(400, diagrams.NOT_IMAGE)
    with db.connect() as conn:
        if not conn.execute("select 1 from clients where id=%s for update", (cid,)).fetchone():
            raise HTTPException(404, "no such client")
        conn.execute("insert into client_logos(client_id, raw) values (%s,%s) on conflict (client_id) do update "
                     "set raw=excluded.raw, png=null, width=0, height=0, reason='', updated_at=now()", (cid, raw))
        conn.execute("insert into jobs(kind, payload) values ('logo', jsonb_build_object('client_id', %s::bigint))", (cid,))
        _logo_audit(conn, cid, "upload", user)
    return RedirectResponse("/admin/clients", status_code=303)


@router.post("/admin/clients/{cid}/logo/remove")
def remove_logo(cid: int, user: User = Depends(require("admin"))):
    with db.connect() as conn:
        if not conn.execute("delete from client_logos where client_id=%s", (cid,)).rowcount:
            raise HTTPException(404, "no logo")
        _logo_audit(conn, cid, "remove", user)
    return RedirectResponse("/admin/clients", status_code=303)


@router.get("/admin/clients/{cid}/logo.png")
def logo_preview(cid: int, user: User = Depends(require("admin"))):
    with db.connect() as conn:  # the admin sees a decoded logo whatever the display rule says; raw is never selected
        row = conn.execute("select png from client_logos where client_id=%s and png is not null", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "no logo")
    return Response(bytes(row[0]), media_type="image/png", headers=PNG_HEADERS)


@router.get("/logos/{cid}.png")
def logo_public(cid: int, user: User = Depends(require("user"))):
    with db.connect() as conn:
        png = logo_for(conn, cid)
    if png is None:
        raise HTTPException(404, "no logo")
    return Response(png, media_type="image/png", headers=PNG_HEADERS)
