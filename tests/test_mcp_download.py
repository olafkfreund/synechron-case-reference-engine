"""Signed download links (#249 phase 2)."""
import logging
import uuid
import time

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from app import db, main, mcp_server
from app.review import SOURCE_KEYS
from tests.test_auth import client as portal_client
from tests.test_mcp_http import env, token  # noqa: F401
from tests.test_review import DOCS, make  # noqa: F401
from tests.test_search import approved, data  # noqa: F401

GROUPS = ["g-user", DOCS]
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def link(who="o1", kind="case", fmt="md", **ids):
    return f"/mcp/download/{mcp_server.sign_download(who, kind, fmt, **ids)}"


def bearer(who="o1", groups=GROUPS, **over):
    return {"Authorization": f"Bearer {token(oid=who, groups=groups, **over)}"}


def gens(who):
    with db.connect() as c:
        return c.execute("select count(*) from generations where user_id = %s", (who,)).fetchone()[0]


@pytest.fixture
def web(env):  # noqa: F811
    with TestClient(main.create_app(), follow_redirects=False) as c:
        yield c


def test_session_and_bearer_give_the_same_file(web, approved):
    cid = approved()
    url = link(case_ids=[cid])
    by_token = web.get(url, headers=bearer())
    web.cookies.clear()
    session = portal_client(GROUPS, sub="o1").get(url)
    assert by_token.status_code == session.status_code == 200
    assert by_token.content == session.content
    assert by_token.headers["cache-control"] == "no-store" and by_token.headers["x-content-type-options"] == "nosniff"
    assert by_token.headers["content-disposition"] == 'attachment; filename="reference-cases.md"'


def test_download_writes_the_audit_rows_then(web, approved):
    cid, who = approved(), f"o-{uuid.uuid4()}"
    url = link(who, case_ids=[cid])
    assert gens(who) == 0  # the link alone writes nothing
    assert web.get(url, headers=bearer(who)).status_code == 200
    assert gens(who) == 1
    with db.connect() as c:
        row = c.execute("select client_id, outcome, object_ids from tool_calls where user_key = %s and tool = 'download'", (who,)).fetchone()
    assert row == ("client-1", "ok", [cid])


def test_another_user_gets_403_and_no_file(web, approved):
    url = link("o1", case_ids=[approved()])
    other = f"o-{uuid.uuid4()}"
    r = web.get(url, headers=bearer(other))
    assert r.status_code == 403 and gens(other) == 0
    assert portal_client(GROUPS, sub=other).get(url).status_code == 403 and gens(other) == 0


def test_no_credentials_and_unapproved_client_are_refused(web, approved):
    url = link(case_ids=[approved()])
    assert web.get(url).status_code == 401
    assert web.get(url, headers=bearer(azp="rogue")).status_code == 403
    assert web.get(url, headers={"Authorization": "Bearer junk"}).status_code == 401


def test_expired_and_tampered_tokens_give_410(web, approved, monkeypatch):
    cid = approved()
    t = mcp_server.sign_download("o1", "case", "md", case_ids=[cid])
    assert web.get(f"/mcp/download/{t[:-2]}xx", headers=bearer()).status_code == 410
    assert web.get("/mcp/download/garbage", headers=bearer()).status_code == 410
    forged = URLSafeTimedSerializer("another-secret", salt="mcp-download").dumps({"k": "o1", "kind": "case", "f": "md", "case_ids": [cid]})
    assert web.get(f"/mcp/download/{forged}", headers=bearer()).status_code == 410
    later = time.time() + mcp_server.DOWNLOAD_TTL + 5
    monkeypatch.setattr("itsdangerous.timed.time.time", lambda: later)
    r = web.get(f"/mcp/download/{t}", headers=bearer())
    assert r.status_code == 410 and "expired" in r.json()["detail"]


def test_case_hidden_after_the_link_was_made_gives_404(web, approved):
    cid = approved()
    who = f"o-{uuid.uuid4()}"
    url = link(who, case_ids=[cid])
    with db.connect() as c:
        c.execute("update cases set review_due = now() - interval '1 day' where id = %s", (cid,))
    r = web.get(url, headers=bearer(who))
    assert r.status_code == 404 and gens(who) == 0


def test_a_user_who_cannot_see_the_case_gets_404(web, approved):
    url = link("o-blind", case_ids=[approved()])
    assert web.get(url, headers=bearer("o-blind", groups=["g-user"])).status_code == 404


def test_docx_has_its_own_type(web, approved):
    r = web.get(link(fmt="docx", case_ids=[approved()]), headers=bearer())
    assert r.status_code == 200 and r.headers["content-type"] == DOCX and r.content[:2] == b"PK"
    assert web.get(link(fmt="nope", case_ids=[approved()]), headers=bearer()).status_code == 400


def seed_brief(who, flags=()):
    q = [{k: "x" for k in SOURCE_KEYS} | {"quote": "Entra offers passwordless sign in", "type": "public_web"}]
    body = {"title": "A brief", "takeaways": ["Passwordless works [1]."], "themes": [{"heading": "Sign in", "text": "It works [1]."}],
            "flags": list(flags)}
    from psycopg.types.json import Jsonb
    with db.connect() as c:
        bid = c.execute("insert into briefs(created_by, title, quotes, body, status) values (%s,'A brief',%s,%s,'done') returning id",
                        (who, Jsonb(q), Jsonb(body))).fetchone()[0]
        lid = c.execute("insert into library_items(topic, kind, body, created_by) values ('t','brief',%s,%s) returning id",
                        (Jsonb({"quotes": q, "brief": {k: body[k] for k in ("title", "takeaways", "themes")}}), who)).fetchone()[0]
    return bid, lid


def test_brief_and_library_kinds(web, env):  # noqa: F811
    who = f"o-{uuid.uuid4()}"
    bid, lid = seed_brief(who)
    b = web.get(link(who, "brief", "md", brief_id=bid), headers=bearer(who))
    assert b.status_code == 200 and b.headers["content-disposition"] == f'attachment; filename="brief-{bid}.md"' and b"A brief" in b.content
    lb = web.get(link(who, "library", "docx", library_id=lid), headers=bearer(who))
    assert lb.status_code == 200 and lb.headers["content-disposition"] == f'attachment; filename="library-{lid}.docx"' and lb.content[:2] == b"PK"
    assert gens(who) == 2
    other_bid, _ = seed_brief("someone")  # a brief is private to its creator
    assert web.get(link(who, "brief", "md", brief_id=other_bid), headers=bearer(who)).status_code == 404
    assert web.get(link(who, "bogus", "md"), headers=bearer(who)).status_code == 410


def test_token_is_in_no_log_and_no_tool_call(web, approved, caplog):
    cid, who = approved(), f"o-{uuid.uuid4()}"
    t = mcp_server.sign_download(who, "case", "md", case_ids=[cid])
    with caplog.at_level(logging.DEBUG):
        assert web.get(f"/mcp/download/{t}", headers=bearer(who)).status_code == 200
        assert web.get(f"/mcp/download/{t[:-2]}xx", headers=bearer(who)).status_code == 410
    ours = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("http"))  # the test client's own log is not the server's
    assert t[:-2] not in ours
    with db.connect() as c:
        rows = c.execute("select * from tool_calls where user_key = %s", (who,)).fetchall()
    assert rows and t not in repr(rows)


def test_access_log_filter_masks_the_token(web):
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/mcp/download/SECRETtoken.abc?x=1", "1.1", 200), None)
    assert mcp_server.MaskDownloadToken().filter(rec) and "SECRET" not in rec.getMessage()
    assert "/mcp/download/***?x=1" in rec.getMessage()
    assert any(isinstance(f, mcp_server.MaskDownloadToken) for f in logging.getLogger("uvicorn.access").filters)
