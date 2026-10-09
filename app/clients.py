import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg.errors import UniqueViolation

from app import anonymise, db
from app.main import User, require
from app.review import page

router = APIRouter()
# the version an admin saw; a concurrent edit in between returns 409 instead of being overwritten
VERSION = "md5(row(name, aliases, anonymised_label, referenceable, logo_allowed)::text)"


def clean(name, aliases, label, cid=None):
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
    # a name or alias shared with another client would attribute cases to the wrong client
    with db.connect() as conn:
        taken = conn.execute("select name, aliases from clients where id is distinct from %s", (cid,)).fetchall()
    taken_keys = {anonymise._key(n) for nm, al in taken for n in [nm, *al]}
    if any(anonymise._key(n) in taken_keys for n in [name, *aliases]):
        raise HTTPException(400, "a name or alias is already used by another client")
    return name, aliases, label


@router.get("/admin/clients")
def clients_page(request: Request, user: User = Depends(require("admin"))):
    with db.connect() as conn:
        rows = conn.execute(f"select id, name, aliases, anonymised_label, referenceable, logo_allowed, "
                            f"{VERSION} from clients order by name").fetchall()
    return page(request, "clients.html", user, clients=rows)


@router.post("/admin/clients")
def create(name: str = Form(), aliases: str = Form(""), anonymised_label: str = Form(),
           referenceable: bool = Form(False), logo_allowed: bool = Form(False),
           next: str = Form(""), user: User = Depends(require("admin"))):
    name, alias_list, label = clean(name, aliases, anonymised_label)
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
        if not conn.execute("select 1 from clients where id=%s", (cid,)).fetchone():
            raise HTTPException(404, "no such client")
    name, alias_list, label = clean(name, aliases, anonymised_label, cid)
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
    return RedirectResponse("/admin/clients", status_code=303)
