import uuid
from datetime import date, timedelta

from app import db
from tests.test_auth import ADMIN, REV, client, env  # noqa: F401
from tests.test_review import DOCS, R, make  # noqa: F401


def cleanup(model):
    with db.connect() as c:
        c.execute("delete from model_approvals where model=%s", (model,))


def test_approvals_admin_only_bounded_and_revocable(env):  # noqa: F811
    db.init()
    model = f"openai/m-{uuid.uuid4().hex[:8]}"
    a, day = client([ADMIN]), lambda d: (date.today() + timedelta(days=d)).isoformat()
    form = lambda d, **kw: {"model": model, "data_class": "confidential", "expires": day(d), "note": "n", **kw}  # noqa: E731
    try:
        assert client([REV]).get("/admin/models").status_code == 403
        assert client([REV]).post("/admin/models", data=form(30)).status_code == 403
        assert a.post("/admin/models", data=form(400)).status_code == 400  # beyond 12 months
        assert a.post("/admin/models", data=form(-1)).status_code == 400  # already over
        assert a.post("/admin/models", data=form(30, expires="soon")).status_code == 400
        assert a.post("/admin/models", data=form(30, data_class="secret")).status_code == 400
        assert a.post("/admin/models", data=form(30), follow_redirects=False).status_code == 303
        with db.connect() as c:
            aid = c.execute("select id from model_approvals where model=%s", (model,)).fetchone()[0]
        assert model in a.get("/admin/models").text and model in a.get("/admin/audit").text
        from app import llm
        assert llm.allowed("third-party", "confidential", model)
        assert client([REV]).post(f"/admin/models/{aid}/revoke").status_code == 403
        assert a.post(f"/admin/models/{aid}/revoke", follow_redirects=False).status_code == 303
        assert not llm.allowed("third-party", "confidential", model)
        assert a.post(f"/admin/models/{aid}/revoke").status_code == 404  # already ended
        assert "(expired)" in a.get("/admin/audit").text
    finally:
        cleanup(model)


def test_source_data_class_saved_and_validated(env):  # noqa: F811
    db.init()
    name, a = f"s3-{uuid.uuid4().hex[:8]}", client([ADMIN])
    base = {"kind": "s3", "name": name, "config": '{"bucket": "b"}', "acl_groups": "g1"}
    assert a.post("/admin/sources", data={**base, "data_class": "secret"}).status_code == 400
    assert a.post("/admin/sources", data=base, follow_redirects=False).status_code == 303
    def get():
        with db.connect() as c:  # closed: an open transaction would block test_concurrent_init's drop schema
            return c.execute("select id, data_class from sources where name=%s", (name,)).fetchone()
    sid, dc = get()
    try:
        assert dc == "confidential"  # the default
        assert a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1", "data_class": "bogus"}).status_code == 400
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1", "data_class": "public"})
        assert get()[1] == "public"
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1"})  # no value: unchanged
        assert get()[1] == "public"
        assert "selected" in a.get("/admin/sources").text
    finally:
        with db.connect() as c:
            c.execute("delete from sources where id=%s", (sid,))


def test_lowering_a_source_class_is_logged_and_audited(env):  # noqa: F811
    db.init()
    name, a = f"s3-{uuid.uuid4().hex[:8]}", client([ADMIN])
    a.post("/admin/sources", data={"kind": "s3", "name": name, "config": '{"bucket": "b"}', "acl_groups": "g1"})
    with db.connect() as c:
        sid = c.execute("select id from sources where name=%s", (name,)).fetchone()[0]
    try:
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1", "data_class": "public"})
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1", "data_class": "confidential"})
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g1"})  # unchanged: no row
        with db.connect() as c:
            rows = c.execute("select old_class, new_class, changed_by from source_class_changes "
                             "where source_id=%s order by id", (sid,)).fetchall()
        assert rows == [("confidential", "public", "u1"), ("public", "confidential", "u1")]  # a flip back stays visible
        assert name in a.get("/admin/audit").text
    finally:
        with db.connect() as c:
            c.execute("delete from source_class_changes where source_id=%s", (sid,))
            c.execute("delete from sources where id=%s", (sid,))


def test_removing_a_source_group_applies_to_documents_at_once_and_is_logged(make):  # noqa: F811
    cid = make(acl=("g-docs", "g-other"))
    reader, admin = client([REV, "g-docs"]), client([ADMIN])  # before the stale state: client() runs db.init(), which repairs it
    with db.connect() as c:
        sid = c.execute("select d.source_id from cases c join documents d on d.id=c.document_id where c.id=%s",
                        (cid,)).fetchone()[0]
        c.execute("update sources set acl_groups='{g-docs,g-other}' where id=%s", (sid,))
    a, reader = client([ADMIN]), client([REV, "g-docs"])
    assert reader.get(f"/review/{cid}").status_code == 200
    a.post(f"/admin/sources/{sid}", data={"acl_groups": "g-other"})
    a.post(f"/admin/sources/{sid}", data={"acl_groups": "g-other"})  # same groups again: no row
    page = a.get("/admin/audit").text
    with db.connect() as c:
        assert c.execute("select acl_groups from documents where source_id=%s", (sid,)).fetchone()[0] == ["g-other"]
        rows = c.execute("select old_groups, new_groups, changed_by from source_acl_changes where source_id=%s",
                         (sid,)).fetchall()
        c.execute("delete from source_acl_changes where source_id=%s", (sid,))
    assert rows == [(["g-docs", "g-other"], ["g-other"], "u1")]
    assert "Access group changes" in page and "g-docs, g-other" in page
    assert reader.get(f"/review/{cid}").status_code == 404


def test_stale_document_groups_repaired_by_resave_and_by_migration(make):  # noqa: F811
    # before #58 a crawl could write old groups back: source {g-other}, documents still {g-docs, g-other}
    cid = make(acl=("g-docs", "g-other"))
    reader, admin = client([REV, "g-docs"]), client([ADMIN])  # before the stale state: client() runs db.init(), which repairs it
    with db.connect() as c:
        sid = c.execute("select d.source_id from cases c join documents d on d.id=c.document_id where c.id=%s",
                        (cid,)).fetchone()[0]
        c.execute("update sources set acl_groups='{g-other}' where id=%s", (sid,))
    assert reader.get(f"/review/{cid}").status_code == 200
    admin.post(f"/admin/sources/{sid}", data={"acl_groups": "g-other"})  # same groups: still repairs
    assert reader.get(f"/review/{cid}").status_code == 404
    with db.connect() as c:
        assert c.execute("select count(*) from source_acl_changes where source_id=%s", (sid,)).fetchone()[0] == 0
        c.execute("update documents set acl_groups='{g-docs,g-other}' where source_id=%s", (sid,))
    db.init()  # the migration repairs it too
    assert reader.get(f"/review/{cid}").status_code == 404


def test_approvals_only_for_confidential_and_revoker_recorded(env):  # noqa: F811
    db.init()
    model, a = f"openai/m-{uuid.uuid4().hex[:8]}", client([ADMIN])
    exp = (date.today() + timedelta(days=30)).isoformat()
    try:
        assert a.post("/admin/models", data={"model": model, "data_class": "public", "expires": exp}).status_code == 400
        assert a.post("/admin/models", data={"model": model, "data_class": "confidential", "expires": exp},
                      follow_redirects=False).status_code == 303
        with db.connect() as c:
            aid = c.execute("select id from model_approvals where model=%s", (model,)).fetchone()[0]
        a.post(f"/admin/models/{aid}/revoke")
        with db.connect() as c:
            assert c.execute("select revoked_by from model_approvals where id=%s", (aid,)).fetchone()[0] == "u1"
    finally:
        cleanup(model)


def test_new_admin_posts_need_login_and_same_origin(env):  # noqa: F811
    exp = (date.today() + timedelta(days=30)).isoformat()
    assert client().post("/admin/models", data={"model": "m", "data_class": "confidential", "expires": exp}).status_code == 401
    bad = client([ADMIN], origin="https://evil.example.com")
    assert bad.post("/admin/models", data={"model": "m", "data_class": "confidential", "expires": exp}).status_code == 403
    assert bad.post("/admin/models/1/revoke").status_code == 403
