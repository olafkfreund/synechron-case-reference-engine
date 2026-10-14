import uuid
from io import BytesIO

import pytest
from PIL import Image

from app import clients, db, diagrams
from tests.test_auth import ADMIN, REV, USER, client, env  # noqa: F401


def img(fmt="PNG", size=(200, 100)):
    b = BytesIO()
    Image.new("RGB", size, "red").save(b, fmt)
    return b.getvalue()


@pytest.fixture
def cl(env):
    db.init()
    made = []

    def make(ref=True, allowed=True, png=True):
        with db.connect() as c:
            cid = c.execute("insert into clients(name,anonymised_label,referenceable,logo_allowed) values (%s,'a bank',%s,%s) returning id",
                            (uuid.uuid4().hex, ref, allowed)).fetchone()[0]
            if png:
                c.execute("insert into client_logos(client_id,png,width,height) values (%s,%s,200,100)", (cid, img()))
        made.append(cid)
        return cid
    yield make
    with db.connect() as c:
        for i in made:
            c.execute("delete from clients where id=%s", (i,))
            c.execute("delete from client_logo_changes where client_id=%s", (i,))
        c.execute("delete from jobs where kind='logo'")


@pytest.mark.parametrize("ref,allowed,decoded,shown", [
    (True, True, True, True), (True, True, False, False), (True, False, True, False), (False, True, True, False),
    (False, False, True, False), (True, False, False, False), (False, True, False, False), (False, False, False, False),
])
def test_logo_for_truth_table(cl, ref, allowed, decoded, shown):
    cid = cl(ref, allowed, png=False)
    with db.connect() as c:
        c.execute("insert into client_logos(client_id,png,raw) values (%s,%s,%s)", (cid, img() if decoded else None, None if decoded else b"x"))
        got = clients.logo_for(c, cid)
    assert (got is not None) is shown


def test_public_route_follows_the_rule(cl):
    ok, protected, nolicence = cl(), cl(ref=False), cl(allowed=False)
    u = client([USER])
    r = u.get(f"/logos/{ok}.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"
    assert u.get(f"/logos/{protected}.png").status_code == 404 and u.get(f"/logos/{nolicence}.png").status_code == 404
    assert client().get(f"/logos/{ok}.png").status_code == 401


def test_admin_preview_is_admin_only_and_ignores_the_rule(cl):
    cid = cl(allowed=False)
    assert client([ADMIN]).get(f"/admin/clients/{cid}/logo.png").status_code == 200
    assert client([REV]).get(f"/admin/clients/{cid}/logo.png").status_code == 403


def test_upload_queues_a_job_and_audits(cl):
    cid = cl(png=False)
    r = client([ADMIN]).post(f"/admin/clients/{cid}/logo", files={"file": ("l.png", img())}, follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as c:
        assert c.execute("select raw is not null, png is null from client_logos where client_id=%s", (cid,)).fetchone() == (True, True)
        assert c.execute("select count(*) from jobs where kind='logo' and payload->>'client_id'=%s", (str(cid),)).fetchone()[0] == 1
        assert c.execute("select action, changed_by from client_logo_changes where client_id=%s", (cid,)).fetchall() == [("upload", "u1")]
    assert "Client logo changes" in client([ADMIN]).get("/admin/audit").text


def test_upload_refusals(cl):
    cid = cl(png=False)
    a = client([ADMIN])
    url = f"/admin/clients/{cid}/logo"
    assert a.post(url, files={"file": ("l.svg", b"<svg onload=x>")}).status_code == 400
    assert a.post(url, files={"file": ("l.png", b"GIF89a")}).status_code == 400
    assert a.post(url, files={"file": ("l.png", img() + b"0" * diagrams.LOGO_MAX_BYTES)}).status_code == 413
    assert a.post("/admin/clients/999999999/logo", files={"file": ("l.png", img())}).status_code == 404
    assert client([REV]).post(url, files={"file": ("l.png", img())}).status_code == 403
    with db.connect() as c:
        assert not c.execute("select 1 from client_logos where client_id=%s", (cid,)).fetchone()
        assert not c.execute("select 1 from client_logo_changes where client_id=%s", (cid,)).fetchone()


def test_remove_deletes_and_audits(cl):
    cid = cl()
    assert client([REV]).post(f"/admin/clients/{cid}/logo/remove").status_code == 403
    assert client([ADMIN]).post(f"/admin/clients/{cid}/logo/remove", follow_redirects=False).status_code == 303
    assert client([ADMIN]).post(f"/admin/clients/{cid}/logo/remove").status_code == 404
    with db.connect() as c:
        assert not c.execute("select 1 from client_logos where client_id=%s", (cid,)).fetchone()
        assert c.execute("select action from client_logo_changes where client_id=%s", (cid,)).fetchall() == [("remove",)]


def test_guard_caps_the_logo_body_before_parsing(cl):
    cid = cl(png=False)
    r = client([ADMIN]).post(f"/admin/clients/{cid}/logo", files={"file": ("l.png", img() + b"0" * 2 * diagrams.LOGO_MAX_BYTES)})
    assert r.status_code == 413 and "larger than" in r.text


def test_registry_page_shows_the_logo_state(cl):
    ready, nologo = cl(), cl(png=False)
    with db.connect() as c:
        c.execute("insert into client_logos(client_id,reason) values (%s,%s)", (nologo, diagrams.LOGO_TOO_SMALL))
    t = client([ADMIN]).get("/admin/clients").text
    assert "Logo ready" in t and f'src="/admin/clients/{ready}/logo.png"' in t and f'action="/admin/clients/{ready}/logo/remove"' in t
    assert "Rejected: " + diagrams.LOGO_TOO_SMALL in t and f'src="/admin/clients/{nologo}/logo.png"' not in t
