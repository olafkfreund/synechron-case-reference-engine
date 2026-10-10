import json
import re
import time
import uuid
from base64 import b64decode, b64encode

import boto3
import pytest
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from moto import mock_aws

from app import db, main

SECRET = "test-secret"
ORIGIN = "http://testserver"
DOCX, PDF = b"PK\x03\x04rest", b"%PDF-1.7 rest"
ADMIN, REV, USER = "g-admin", "g-rev", "g-user"


@pytest.fixture
def env(monkeypatch):
    for k, v in dict(SESSION_SECRET=SECRET, SESSION_HTTPS_ONLY="false", APP_ORIGIN=ORIGIN, ROLE_ADMIN_GROUPS=ADMIN,
                     ROLE_REVIEWER_GROUPS=REV, ROLE_USER_GROUPS=USER, AWS_ACCESS_KEY_ID="x",
                     AWS_SECRET_ACCESS_KEY="x", AWS_DEFAULT_REGION="us-east-1").items():
        monkeypatch.setenv(k, v)


def client(groups=None, origin=ORIGIN, sub="u1", name="U", iat=None):
    c = TestClient(main.create_app(), headers={"Origin": origin} if origin else {})
    if groups is not None:  # sign the session cookie the way SessionMiddleware does
        db.init()
        u = {"sub": sub, "name": name, "groups": groups, "iat": time.time() if iat is None else iat}
        if iat is False:
            del u["iat"]
        data = b64encode(json.dumps({"user": u}).encode())
        c.cookies.set("session", TimestampSigner(SECRET).sign(data).decode())
    return c


def test_roles_inherit(env):
    assert main.roles_for([ADMIN]) == {"user", "reviewer", "admin"}
    assert main.roles_for([REV]) == {"user", "reviewer"}
    assert main.roles_for([USER]) == {"user"}
    assert main.roles_for(["other"]) == frozenset()


def test_missing_secret_fails_at_start(env, monkeypatch):
    monkeypatch.delenv("SESSION_SECRET")
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        main.create_app()


def test_anonymous_is_401_and_headers_are_ignored(env):
    c = client()
    assert c.get("/me").status_code == 401
    assert c.get("/me", headers={"X-Groups": ADMIN, "X-Forwarded-Groups": ADMIN}).status_code == 401
    assert c.post("/admin/upload", headers={"X-Groups": ADMIN}).status_code == 401


def test_header_and_query_cannot_raise_roles(env):
    c = client([USER])
    r = c.post("/admin/upload?groups=" + ADMIN, headers={"X-Groups": ADMIN},
               files={"file": ("a.docx", DOCX)})
    assert r.status_code == 403
    assert c.get("/me", headers={"X-Groups": ADMIN}).json()["roles"] == ["user"]


def test_tampered_cookie_rejected(env):
    c = client()
    c.cookies.set("session", "eyJ1c2VyIjp7fX0.bad.signature")
    assert c.get("/me").status_code == 401


def test_login_without_oidc_is_clear_error(env, monkeypatch):
    monkeypatch.delenv("OIDC_METADATA_URL", raising=False)
    r = client().get("/login", follow_redirects=False)
    assert r.status_code == 503 and "OIDC is not configured" in r.text


def test_callback_takes_groups_from_token_claim(env, monkeypatch):
    for k, v in dict(OIDC_METADATA_URL="http://idp/.well-known", OIDC_CLIENT_ID="c",
                     OIDC_CLIENT_SECRET="s", OIDC_GROUPS_CLAIM="roles").items():
        monkeypatch.setenv(k, v)
    app = main.create_app()

    async def fake(request):
        return {"userinfo": {"sub": "u9", "name": "N", "roles": [REV], "groups": [ADMIN], "email": "e"}}
    monkeypatch.setattr(app.state.oauth.oidc, "authorize_access_token", fake)
    c = TestClient(app, headers={"Origin": ORIGIN})
    assert c.get("/auth", headers={"X-Groups": ADMIN}, follow_redirects=False).status_code == 303
    me = c.get("/me").json()
    assert me["roles"] == ["reviewer", "user"] and me["sub"] == "u9"
    assert c.get("/logout").status_code == 405  # GET must not log out
    c.post("/logout")
    assert c.get("/me").status_code == 401


@pytest.fixture
def upload_src(env):
    db.init()
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="upload-bkt")
        with db.connect() as c:
            sid = c.execute("insert into sources(kind,name,config) values ('upload',%s,%s) returning id",
                            (uuid.uuid4().hex, '{"bucket":"upload-bkt","prefix":"uploads/"}')).fetchone()[0]
            c.execute("delete from jobs")
        yield sid
        with db.connect() as c:
            c.execute("delete from sources where id=%s", (sid,))
            c.execute("delete from jobs")


def test_upload_good_file_lands_in_s3_and_queues_crawl(upload_src):
    r = client([ADMIN]).post("/admin/upload", files={"file": ("..\\..\\etc/My Case!.DOCX", DOCX)})
    assert r.status_code == 202
    keys = [o["Key"] for o in boto3.client("s3").list_objects_v2(Bucket="upload-bkt")["Contents"]]
    assert len(keys) == 1
    k = keys[0]
    assert re.fullmatch(r"uploads/[0-9a-f]{32}/My_Case_\.DOCX", k) and ".." not in k and "etc" not in k
    with db.connect() as c:
        assert c.execute("select id, kind, payload from jobs").fetchall() == [
            (r.json()["job_id"], "crawl_s3", {"source_id": upload_src})]


def test_upload_rejects_bad_extension_and_oversize(upload_src, monkeypatch):
    c = client([ADMIN])
    assert c.post("/admin/upload", files={"file": ("a.exe", b"x")}).status_code == 400
    assert c.post("/admin/upload", files={"file": ("a.html", b"<html>")}).status_code == 400
    assert c.post("/admin/upload", files={"file": ("a.pdf", DOCX)}).status_code == 400  # magic mismatch
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "10")
    assert c.post("/admin/upload", files={"file": ("a.pdf", PDF + b"x" * 200_000)}).status_code == 413
    assert "Contents" not in boto3.client("s3").list_objects_v2(Bucket="upload-bkt")
    with db.connect() as conn:
        assert conn.execute("select count(*) from jobs").fetchone()[0] == 0


def test_upload_without_source_is_400(env):
    cl = client([ADMIN])  # before the open connection: client() runs db.init()
    with mock_aws(), db.connect() as c:
        c.execute("delete from sources where kind='upload'")
        assert cl.post("/admin/upload", files={"file": ("a.pdf", PDF)}).status_code == 400


def test_oversize_anonymous_body_refused_before_parsing(env, monkeypatch):
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "1000")
    r = client().post("/admin/upload", content=b"x" * 500_000,
                      headers={"Content-Type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413  # not 401: refused before the body is read
    r = client().post("/admin/upload", content=iter([b"x"]),  # chunked: no length
                      headers={"Content-Type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413


def test_csrf_origin_required_on_unsafe_methods(env):
    assert client([ADMIN], origin="https://evil.corp.example.com").post("/logout").status_code == 403
    assert client([ADMIN], origin=None).post("/logout").status_code == 403
    c = client([ADMIN], origin=None)
    assert c.post("/logout", headers={"Sec-Fetch-Site": "same-origin"}, follow_redirects=False).status_code == 303
    assert client([ADMIN], origin=None).get("/me").status_code == 200  # safe methods unaffected


def test_weak_secret_and_missing_origin_fail_at_start(env, monkeypatch):
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "true")
    with pytest.raises(RuntimeError, match="32 characters"):
        main.create_app()
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")
    monkeypatch.delenv("APP_ORIGIN")
    with pytest.raises(RuntimeError, match="APP_ORIGIN"):
        main.create_app()


def oidc_app(monkeypatch, userinfo=None, error=False):
    for k, v in dict(OIDC_METADATA_URL="http://idp/.well-known", OIDC_CLIENT_ID="c",
                     OIDC_CLIENT_SECRET="s").items():
        monkeypatch.setenv(k, v)
    app = main.create_app()

    async def fake(request):
        if error:
            raise main.OAuthError("access_denied")
        return {"userinfo": userinfo}
    monkeypatch.setattr(app.state.oauth.oidc, "authorize_access_token", fake)
    return TestClient(app, headers={"Origin": ORIGIN})


def test_groups_overage_refused_and_unknown_groups_dropped(env, monkeypatch):
    db.init()
    c = oidc_app(monkeypatch, {"sub": "u1", "_claim_names": {"groups": "src1"}})
    assert c.get("/auth", follow_redirects=False).status_code == 403
    c = oidc_app(monkeypatch, {"sub": "u1", "groups": [USER] + [f"noise-{i}" for i in range(300)]})
    assert c.get("/auth", follow_redirects=False).status_code == 303
    assert len(c.cookies["session"]) < 1000 and c.get("/me").json()["roles"] == ["user"]


def test_cancelled_login_is_401_not_500(env, monkeypatch):
    assert oidc_app(monkeypatch, error=True).get("/auth").status_code == 401


def test_safe_name_is_never_a_path():  # the upload key is {uuid}/{name} (#110)
    for raw in ("..", ".", "../..", "a/..", "/", "a/b\\c.pdf", "..\\..\\x.docx", ""):
        n = main.safe_name(raw)
        assert "/" not in n and "\\" not in n and n not in ("", ".", "..")


def test_long_filename_truncated():
    n = main.safe_name("a" * 2000 + ".pdf")
    assert n.endswith(".pdf") and len(n) <= 110


def test_healthz_needs_no_login_and_no_database(env, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)  # would raise if the route touched the database
    r = client().get("/healthz")
    assert r.status_code == 200 and r.text == "ok" and "set-cookie" not in r.headers


def fresh():
    return f"t-{uuid.uuid4()}"  # the test database persists: never reuse a sub that may have a cutoff


def cut(*subs):
    with db.connect() as conn:
        main.cut_sessions(conn, *subs)


def test_logout_revokes_replayed_cookie(env):
    sub = fresh()
    a = client([USER], sub=sub)
    b = client()
    b.cookies.set("session", a.cookies["session"])
    assert b.get("/me").status_code == 200
    assert a.post("/logout", follow_redirects=False).status_code == 303
    assert b.get("/me").status_code == 401
    r = b.get("/me", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_login_after_cutoff_works(env):
    sub = fresh()
    cut(sub)
    assert client([USER], sub=sub).get("/me").status_code == 200  # newer iat


def test_cookie_without_iat(env):
    sub = fresh()
    assert client([USER], sub=sub, iat=False).get("/me").status_code == 401  # fail closed (#145)
    cut(sub)
    assert client([USER], sub=sub, iat=False).get("/me").status_code == 401


def test_session_expires_after_max_age(env):
    """Rolling renewal must not keep groups the IdP removed: a session has an absolute lifetime (#145)."""
    old = time.time() - main.SESSION_MAX_AGE - 5
    sub = fresh()  # no cutoff row: only the age can refuse it
    assert client([USER], sub=sub, iat=old).get("/me").status_code == 401
    r = client([USER], sub=sub, iat=old).get("/me", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client([USER], sub=fresh(), iat=time.time() - main.SESSION_MAX_AGE + 60).get("/me").status_code == 200


def test_login_drops_existing_user(env, monkeypatch):
    for k, v in dict(OIDC_METADATA_URL="http://idp/.well-known", OIDC_CLIENT_ID="c",
                     OIDC_CLIENT_SECRET="s").items():
        monkeypatch.setenv(k, v)
    c = client([USER])

    async def fake(request, redirect_uri):
        request.session["_state_oidc_x"] = {"data": {}}  # as authlib does
        return RedirectResponse("http://idp/authorize", status_code=302)
    monkeypatch.setattr(c.app.state.oauth.oidc, "authorize_redirect", fake)
    assert c.get("/me").status_code == 200
    r = c.get("/login", follow_redirects=False)
    assert r.status_code == 302
    sent = json.loads(b64decode(TimestampSigner(SECRET).unsign(r.cookies["session"])))
    assert "_state_oidc_x" in sent and "user" not in sent


def test_cut_sessions_is_per_user(env):
    a, b = fresh(), fresh()
    ca, cb = client([USER], sub=a), client([USER], sub=b)
    cut(a)
    assert ca.get("/me").status_code == 401 and cb.get("/me").status_code == 200


def test_auth_logs_login_line(env, monkeypatch, capsys):
    db.init()
    sub = fresh()
    c = oidc_app(monkeypatch, {"sub": sub, "name": "Test Person", "groups": [USER]})
    assert c.get("/auth", follow_redirects=False).status_code == 303
    assert f"login sub='{sub}' name='Test Person'" in capsys.readouterr().out
    assert c.get("/me").status_code == 200


def test_revoke_sessions_command(env):
    from app import revoke_sessions
    sub = fresh()
    c = client([USER], sub=sub)
    assert revoke_sessions.main([]) == 2
    assert c.get("/me").status_code == 200
    assert revoke_sessions.main([sub]) == 0
    assert c.get("/me").status_code == 401


HTML = {"Accept": "text/html"}


def add_source(kind, enabled=True):
    with db.connect() as c:
        return c.execute("insert into sources(kind,name,config,enabled) values (%s,%s,%s,%s) returning id",
                         (kind, uuid.uuid4().hex, '{"bucket":"upload-bkt","prefix":"other/"}', enabled)).fetchone()[0]


def test_upload_browser_success_redirects(upload_src):
    r = client([ADMIN]).post("/admin/upload", files={"file": ("a.docx", DOCX)}, data={"source_id": upload_src},
                             headers=HTML, follow_redirects=False)  # the form always sends source_id
    with db.connect() as c:
        jobs = c.execute("select id, kind, payload from jobs").fetchall()
    assert len(jobs) == 1 and jobs[0][1:] == ("crawl_s3", {"source_id": upload_src})
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/sources?notice=uploaded&source={upload_src}&job={jobs[0][0]}"
    assert len(boto3.client("s3").list_objects_v2(Bucket="upload-bkt")["Contents"]) == 1


def test_upload_source_id_picks_that_source(upload_src):
    second = add_source("upload")
    try:
        r = client([ADMIN]).post("/admin/upload", files={"file": ("a.docx", DOCX)}, data={"source_id": second})
        assert r.status_code == 202
        with db.connect() as c:
            assert c.execute("select payload from jobs").fetchall() == [({"source_id": second},)]
        assert boto3.client("s3").list_objects_v2(Bucket="upload-bkt")["Contents"][0]["Key"].startswith("other/")
    finally:
        with db.connect() as c:
            c.execute("delete from sources where id=%s", (second,))


def test_upload_source_id_must_be_enabled_upload_source(upload_src):
    s3src, off = add_source("s3"), add_source("upload", enabled=False)
    try:
        for sid in (s3src, off, -1):
            f = {"file": ("a.docx", DOCX)}
            r = client([ADMIN]).post("/admin/upload", files=f, data={"source_id": sid})
            assert r.status_code == 404 and r.json()["detail"] == "no such enabled upload source"
            r = client([ADMIN]).post("/admin/upload", files=f, data={"source_id": sid}, headers=HTML,
                                     follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == "/admin/sources?notice=source"
        assert "Contents" not in boto3.client("s3").list_objects_v2(Bucket="upload-bkt")
        with db.connect() as c:
            assert c.execute("select count(*) from jobs").fetchone()[0] == 0
    finally:
        with db.connect() as c:
            c.execute("delete from sources where id in (%s,%s)", (s3src, off))


@pytest.mark.parametrize("name, body, code", [("a.exe", b"x", "type"), ("a.pdf", DOCX, "content"),
                                              ("a.pdf", PDF + b"x" * 100, "size")])
def test_upload_browser_refusals(upload_src, monkeypatch, name, body, code):
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "10")  # the body stays under the guard's cap + 64 KiB
    r = client([ADMIN]).post("/admin/upload", files={"file": (name, body)}, headers=HTML, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/admin/sources?notice={code}"
    assert "Contents" not in boto3.client("s3").list_objects_v2(Bucket="upload-bkt")
    with db.connect() as c:
        assert c.execute("select count(*) from jobs").fetchone()[0] == 0


def test_upload_browser_without_source(env):
    cl = client([ADMIN])
    with mock_aws(), db.connect() as c:
        c.execute("delete from sources where kind='upload'")
        r = cl.post("/admin/upload", files={"file": ("a.pdf", PDF)}, headers=HTML, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/sources?notice=nosource"
