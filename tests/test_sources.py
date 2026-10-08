import uuid

from app import db
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
