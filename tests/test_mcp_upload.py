"""Uploads over MCP (#249 phase 3): the tool, the bearer-only PUT, the status."""
import logging
import time
import uuid

import boto3
import jsonschema
import pytest
from itsdangerous import URLSafeTimedSerializer
from moto import mock_aws

from app import db, mcp_server
from tests.test_auth import client as portal_client
from tests.test_mcp_http import rpc, token  # noqa: F401
from tests.test_mcp_tools import GROUPS, PREFACE_START, mcp, parse  # noqa: F401
from tests.test_mcp_http import env  # noqa: F401
from tests.test_review import make  # noqa: F401

DOCX = b"PK\x03\x04body"


@pytest.fixture
def staff(env):  # noqa: F811
    db.init()
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="src")
        with db.connect() as c:
            sid = c.execute("insert into sources(kind,name,config) values ('upload',%s,%s) returning id",
                            (uuid.uuid4().hex, '{"bucket":"src","prefix":"up/","staff":true}')).fetchone()[0]
        yield sid
        with db.connect() as c:
            c.execute("delete from jobs where payload->>'source_id' = %s", (str(sid),))
            c.execute("delete from uploads where source_id=%s", (sid,))
            c.execute("delete from sources where id=%s", (sid,))


def url_of(s, name="a.docx", size=len(DOCX)):
    out = s.out("upload_document", filename=name, size=size)
    return out["upload_url"].removeprefix("http://testserver")


def put(s, url, data=DOCX, tok=None, **kw):
    return s.c.put(url, content=data, headers={"Authorization": f"Bearer {tok or s.tok}"}, **kw)


def test_tool_put_then_status_queued(mcp, staff):
    s = mcp()
    out = s.out("upload_document", filename="a.docx", size=len(DOCX))
    assert out["expires_in"] == 600 and out["portal_url"].endswith("/upload") and "/mcp/upload/" in out["upload_url"]
    r = put(s, out["upload_url"].removeprefix("http://testserver"))
    assert r.status_code == 200
    uid = r.json()["upload_id"]
    got = s.out("get_upload_status", upload_id=uid)
    assert got["status"] == "queued" and got["detail"].startswith("Waiting in line: ")
    with db.connect() as c:
        c.execute("update jobs set status='running' where id=(select job_id from uploads where id=%s)", (uid,))
    got = s.out("get_upload_status", upload_id=uid)
    assert got["status"] == "reading" and "Converting" in got["detail"]
    assert s.out("list_my_uploads")["uploads"][0]["detail"] == got["detail"]
    assert [u["id"] for u in s.out("list_my_uploads")["uploads"]] == [uid]
    assert mcp().call("get_upload_status", upload_id=uid)["isError"]  # another user: not found
    assert ("upload", "ok", [uid]) in [r[:3] for r in s.rows()]


def test_outputs_match_their_schemas(mcp, staff):
    s = mcp()
    tools = {t["name"]: t for t in parse(rpc(s.c, "tools/list", "2025-06-18", tok=s.tok))["tools"]}
    uid = put(s, url_of(s)).json()["upload_id"]
    for name, args in (("upload_document", dict(filename="a.docx", size=3)), ("get_upload_status", dict(upload_id=uid)), ("list_my_uploads", {})):
        res = s.call(name, **args)
        assert not res.get("isError") and tools[name]["outputSchema"], name
        jsonschema.validate(res["structuredContent"], tools[name]["outputSchema"])
        assert res["content"][0]["text"].startswith(PREFACE_START)
    assert tools["get_upload_status"]["annotations"]["readOnlyHint"] and not tools["upload_document"]["annotations"]["readOnlyHint"]


def test_cookie_only_put_is_401(mcp, staff):
    s = mcp()
    r = portal_client(GROUPS, sub=s.who).put(url_of(s), content=DOCX)
    assert r.status_code == 401
    assert s.c.put(url_of(s), content=DOCX).status_code == 401  # no credentials at all


def test_another_user_gets_403_and_nothing_is_stored(mcp, staff):
    s, other = mcp(), mcp()
    assert put(other, url_of(s)).status_code == 403
    with db.connect() as c:
        assert c.execute("select count(*) from uploads where user_key in (%s,%s)", (s.who, other.who)).fetchone()[0] == 0


def test_size_mismatch_is_400(mcp, staff):
    s = mcp()
    assert put(s, url_of(s, size=len(DOCX) + 1)).status_code == 400
    assert put(s, url_of(s), data=DOCX + b"x").status_code == 400


def test_expired_and_tampered_are_410(mcp, staff, monkeypatch):
    s = mcp()
    u = url_of(s)
    assert put(s, u[:-2] + "xx").status_code == 410
    forged = URLSafeTimedSerializer("another-secret", salt="mcp-upload").dumps({"k": s.who, "n": "a.docx", "s": len(DOCX)})
    assert put(s, f"/mcp/upload/{forged}").status_code == 410
    down = mcp_server.sign_download(s.who, "case", "md")  # the other salt
    assert put(s, f"/mcp/upload/{down}").status_code == 410
    later = time.time() + mcp_server.DOWNLOAD_TTL + 5
    monkeypatch.setattr("itsdangerous.timed.time.time", lambda: later)
    assert put(s, u, tok=token(oid=s.who, groups=GROUPS, exp=later + 600, iat=later - 5)).status_code == 410


def test_bad_content_is_refused_and_audited(mcp, staff):
    s = mcp()
    assert put(s, url_of(s), data=b"nope!!!!!").status_code == 400  # same length, wrong magic
    assert s.rows()[-1][:2] == ("upload", "error")


def test_quota_is_checked_at_tool_time(mcp, staff):
    s = mcp()
    with db.connect() as c:
        c.execute("insert into uploads(user_key, source_id, s3_key, bytes, job_id) select %s, %s, 'k', 1, 1 from generate_series(1, 20)", (s.who, staff))
    res = s.call("upload_document", filename="a.docx", size=3)
    assert res["isError"] and "daily upload limit" in res["content"][0]["text"]
    assert mcp().call("upload_document", filename="a.exe", size=3)["isError"]
    assert mcp().call("upload_document", filename="a.docx", size=10**10)["isError"]


def test_not_configured(mcp, staff):
    with db.connect() as c:
        c.execute("update sources set config = config - 'staff' where id=%s", (staff,))
    res = mcp().call("upload_document", filename="a.docx", size=3)
    assert res["isError"] and "not configured" in res["content"][0]["text"]


def test_token_is_in_no_log_and_no_tool_call(mcp, staff, caplog):
    s = mcp()
    u = url_of(s, "secretname.docx")
    t = u.rsplit("/", 1)[1]
    with caplog.at_level(logging.DEBUG):
        assert put(s, u).status_code == 200
        assert put(s, u[:-2] + "xx").status_code == 410
    ours = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith(("http", "botocore")))  # the client's and S3's own debug logs are not the server's
    assert t[:-2] not in ours and "secretname" not in ours
    with db.connect() as c:
        rows = repr(c.execute("select * from tool_calls where user_key = %s", (s.who,)).fetchall())
    assert t not in rows and "secretname" not in rows


def test_access_log_filter_masks_the_upload_token():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "PUT", "/mcp/upload/SECRETtoken.abc", "1.1", 200), None)
    assert mcp_server.MaskDownloadToken().filter(rec) and "SECRET" not in rec.getMessage() and "/mcp/upload/***" in rec.getMessage()


def test_put_shares_the_rate_limit(mcp, staff):
    s = mcp()
    u = url_of(s)
    with db.connect() as c:
        c.execute("insert into tool_calls(user_key, client_id, tool, object_ids, outcome, ms) "
                  "select %s, 'client-1', 'list_latest', '{}', 'ok', 1 from generate_series(1, 60)", (s.who,))
    assert put(s, u).status_code == 429


def test_guard_caps_the_body_at_the_upload_cap(mcp, staff, monkeypatch):
    s = mcp()
    u = url_of(s)
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "10")
    assert put(s, u, data=b"x" * (70 * 1024)).status_code == 413  # refused before the route reads it


def test_an_uploaded_draft_is_invisible_to_other_users(mcp, make):
    from tests.test_mcp_tools import doc_id
    cid = make(status="extracted")
    did = doc_id(cid)
    with db.connect() as c:
        c.execute("update documents set web_url='https://example.test/x' where id=%s", (did,))
    other = mcp()
    assert other.call("get_case", case_id=cid)["isError"]
    assert str(cid) not in repr(other.out("list_latest")) and str(cid) not in repr(other.out("search_references", bid_text="onboarding"))
    web = portal_client(GROUPS, sub=other.who)
    assert web.get(f"/go/{did}", follow_redirects=False).status_code == 404
    with db.connect() as c:  # once a reviewer approves it, the same user sees it
        c.execute("update cases set status='approved', review_due=now() + interval '30 days', search_text='onboarding', summary='onboarding' where id=%s", (cid,))
    assert not other.call("get_case", case_id=cid).get("isError")
    assert web.get(f"/go/{did}", follow_redirects=False).status_code == 303


def test_quota_is_refused_before_the_body_is_read_or_accepted(mcp, staff, monkeypatch):
    s = mcp()
    url = url_of(s)
    with db.connect() as c:
        c.execute("insert into uploads(user_key, source_id, s3_key, bytes, job_id) select %s, %s, 'k', 1, 1 from generate_series(1, 20)", (s.who, staff))
    monkeypatch.setattr(__import__("app.ingest", fromlist=["x"]), "accept_upload",
                        lambda *a: pytest.fail("accept_upload ran"))
    assert put(s, url).status_code == 429


def test_unexpected_error_is_a_generic_500_audited_once(mcp, staff, monkeypatch):
    from app import ingest
    s = mcp()
    url = url_of(s)

    def boom(*a):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(ingest, "accept_upload", boom)
    r = put(s, url)
    assert r.status_code == 500 and "secret" not in r.text
    assert [r_[1] for r_ in s.rows() if r_[0] == "upload"] == ["error"]
