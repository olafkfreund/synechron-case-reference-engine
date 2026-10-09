import uuid

from psycopg.types.json import Jsonb

from app import db
from app.schema import ReferenceCase, Sourced
from tests.test_auth import ADMIN, REV, client, env  # noqa: F401


def test_sources_admin(env):  # noqa: F811
    db.init()
    name = f"sp-{uuid.uuid4().hex[:8]}"
    a = client([ADMIN])
    assert client([REV]).get("/admin/sources").status_code == 403
    bad = a.post("/admin/sources", data={"kind": "sharepoint", "name": name, "config": '{"tenant_id": "t"}',
                                          "acl_groups": "g1"})
    assert bad.status_code == 400 and "drive_id" in bad.text
    assert a.post("/admin/sources", data={"kind": "sharepoint", "name": name, "acl_groups": " ",
                                          "config": '{"tenant_id": "t", "drive_id": "d"}'}).status_code == 400
    ok = a.post("/admin/sources", follow_redirects=False, data={
        "kind": "sharepoint", "name": name, "config": '{"tenant_id": "t", "drive_id": "d"}', "acl_groups": "g1, g2"})
    assert ok.status_code == 303
    with db.connect() as c:
        sid, acl = c.execute("select id, acl_groups from sources where name=%s", (name,)).fetchone()
    try:
        assert acl == ["g1", "g2"]
        assert a.post(f"/admin/sources/{sid}/crawl", follow_redirects=False).status_code == 303
        with db.connect() as c:
            assert c.execute("select kind from jobs where payload->>'source_id' = %s", (str(sid),)).fetchone()[0] == "crawl_sharepoint"
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g3"})  # unchecked "enabled" disables it
        assert a.post(f"/admin/sources/{sid}/crawl").status_code == 404
        assert name in a.get("/admin/sources").text
        flag = "select config->'executed_contracts' from sources where id=%s"
        with db.connect() as c:
            assert c.execute(flag, (sid,)).fetchone()[0] is False
        a.post(f"/admin/sources/{sid}", data={"acl_groups": "g3", "executed_contracts": "on"})
        with db.connect() as c:
            assert c.execute(flag, (sid,)).fetchone()[0] is True
        assert 'name="executed_contracts" checked' in a.get("/admin/sources").text
    finally:
        with db.connect() as c:
            c.execute("delete from jobs where payload->>'source_id' = %s", (str(sid),))
            c.execute("delete from sources where id=%s", (sid,))


REASON = "source marked executed"


def _src(c, executed):
    cfg = {"bucket": "b", **({"executed_contracts": True} if executed else {})}
    return c.execute("insert into sources(kind, name, config, acl_groups) values ('upload',%s,%s,'{g1}') returning id",
                     (f"up-{uuid.uuid4().hex[:8]}", Jsonb(cfg))).fetchone()[0]


def _doc(c, sid, kind, deleted=False):
    return c.execute("insert into documents(source_id, external_id, checksum, kind, acl_groups, deleted_at) "
                     "values (%s,%s,'x',%s,'{g1}',case when %s then now() end) returning id",
                     (sid, uuid.uuid4().hex, kind, deleted)).fetchone()[0]


def _case(c, did, status, basis, reason):
    data = ReferenceCase(title=Sourced[str](value="T"), basis=basis, basis_reason=reason).model_dump_json()
    return c.execute("insert into cases(document_id, status, basis, data) values (%s,%s,%s,%s) returning id",
                     (did, status, basis, data)).fetchone()[0]


def _job(c, did, reason=REASON, status="queued"):
    c.execute("insert into jobs(kind, payload, status) values ('extract', jsonb_build_object('document_id', %s::bigint, "
              "'basis', 'engagement', 'basis_reason', %s::text), %s)", (did, reason, status))


def _jobs(c, sid):
    return c.execute("select j.payload from jobs j join documents d on d.id=(j.payload->>'document_id')::bigint "
                     "where j.kind='extract' and d.source_id=%s", (sid,)).fetchall()


def _clean(sid):
    with db.connect() as c:
        c.execute("delete from jobs where kind='extract' and (payload->>'document_id')::bigint in "
                  "(select id from documents where source_id=%s)", (sid,))
        c.execute("delete from cases where document_id is null and id in (select merged_into from cases "
                  "where document_id in (select id from documents where source_id=%s))", (sid,))  # merged rows first
        c.execute("delete from sources where id=%s", (sid,))


def test_ticking_executed_queues_contracts_without_a_case(env):  # noqa: F811
    db.init()
    with db.connect() as c:
        sid = _src(c, False)
        a, b, cc, d = _doc(c, sid, "contract"), _doc(c, sid, "contract"), _doc(c, sid, "contract", True), _doc(c, sid, "case")
        _case(c, b, "rejected", "engagement", "unsigned")
        _job(c, a)  # already queued: ticking must not add a second one
    try:
        form = {"acl_groups": "g1", "executed_contracts": "on"}
        for _ in range(2):  # the second save changes nothing and queues nothing more
            assert client([ADMIN]).post(f"/admin/sources/{sid}", data=form, follow_redirects=False).status_code == 303
            with db.connect() as c:
                rows = _jobs(c, sid)
            assert [r[0] for r in rows] == [{"document_id": a, "basis": "engagement", "basis_reason": REASON}]
    finally:
        _clean(sid)


def test_unticking_executed_retires_flagged_engagements(env):  # noqa: F811
    db.init()
    with db.connect() as c:
        sid = _src(c, True)
        e1, e2, e3, e4, e5, e6 = (_doc(c, sid, "contract") for _ in range(6))
        other = _src(c, True)
        c4 = _case(c, _doc(c, other, "contract"), "approved", "engagement", REASON)
        c1 = _case(c, e1, "approved", "engagement", REASON)
        c2 = _case(c, e2, "approved", "engagement", "executed contract")
        c3 = _case(c, e3, "approved", "delivered", "")
        _job(c, e4)
        _job(c, e5, status="running")
        _job(c, e6, reason="executed contract")
        c7 = _case(c, _doc(c, sid, "contract"), "approved", "engagement", "executed contract")
        merged = c.execute(  # an approved merged engagement (#55) of c1 (flagged) and c7 (a real contract)
            "insert into cases(document_id, member_count, basis, status, data) values (null, 2, 'engagement', 'approved', %s) "
            "returning id", (ReferenceCase(title=Sourced[str](value="M", source_quote="m m m m")).model_dump_json(),)).fetchone()[0]
        c.execute("update cases set merged_into=%s where id in (%s,%s)", (merged, c1, c7))
    try:
        assert client([ADMIN]).post(f"/admin/sources/{sid}", data={"acl_groups": "g1"}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            assert c.execute("select status from cases where id=%s", (merged,)).fetchone()[0] == "extracted"
            assert c.execute("select status from cases where id=%s", (c7,)).fetchone()[0] == "approved"
            v = c.execute("select md5(data::text) from cases where id=%s", (merged,)).fetchone()[0]
        assert client([REV, "g1"]).post(f"/review/{merged}/approve", data={"v": v}).status_code == 409  # c1 is rejected
        # un-merging must not revive the retired member
        assert client([REV, "g1"]).post(f"/review/{merged}/unmerge", data={"v": v}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            st = dict(c.execute("select id, status from cases where id in (%s,%s,%s,%s)", (c1, c2, c3, c4)).fetchall())
            assert st == {c1: "rejected", c2: "approved", c3: "approved", c4: "approved"}
            left = c.execute("select (payload->>'document_id')::bigint from jobs where kind='extract' and "
                             "(payload->>'document_id')::bigint in (%s,%s,%s) order by 1", (e4, e5, e6)).fetchall()
            assert [r[0] for r in left] == [e5, e6]
    finally:
        _clean(sid)
        _clean(other)


def test_saving_without_flag_change_does_nothing(env):  # noqa: F811
    db.init()
    with db.connect() as c:
        off, on = _src(c, False), _src(c, True)
        _doc(c, off, "contract")
        e1 = _case(c, _doc(c, on, "contract"), "approved", "engagement", REASON)
    try:
        for sid, data in ((off, {"acl_groups": "g1"}), (on, {"acl_groups": "g1", "executed_contracts": "on"})):
            assert client([ADMIN]).post(f"/admin/sources/{sid}", data=data, follow_redirects=False).status_code == 303
        with db.connect() as c:
            assert _jobs(c, off) == [] and _jobs(c, on) == []
            assert c.execute("select status from cases where id=%s", (e1,)).fetchone()[0] == "approved"
    finally:
        _clean(off)
        _clean(on)
