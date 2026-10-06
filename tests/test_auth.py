import json
import uuid
from base64 import b64encode

import boto3
import pytest
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


def client(groups=None, origin=ORIGIN):
    c = TestClient(main.create_app(), headers={"Origin": origin} if origin else {})
    if groups is not None:  # sign the session cookie the way SessionMiddleware does
        data = b64encode(json.dumps({"user": {"sub": "u1", "name": "U", "groups": groups}}).encode())
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
    assert k.startswith("uploads/") and k.endswith("-My_Case_.DOCX") and ".." not in k and "etc" not in k
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
    db.init()
    with mock_aws(), db.connect() as c:
        c.execute("delete from sources where kind='upload'")
        assert client([ADMIN]).post("/admin/upload", files={"file": ("a.pdf", PDF)}).status_code == 400


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


def test_long_filename_truncated():
    n = main.safe_name("a" * 2000 + ".pdf")
    assert n.endswith(".pdf") and len(n) <= 110
