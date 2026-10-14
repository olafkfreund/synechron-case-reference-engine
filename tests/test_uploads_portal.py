import shutil
import subprocess
import uuid
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from app import db
from tests.test_auth import ORIGIN, USER, client, env  # noqa: F401 - fixture
from tests.test_review import R, make  # noqa: F401 - fixture

HTML = {"Accept": "text/html"}
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
            c.execute("delete from uploads where source_id=%s or user_key like 'x-%%' or user_key in ('u1','u2')", (sid,))
            c.execute("delete from sources where id=%s", (sid,))


def post(c, name="a.docx", data=DOCX, **kw):
    return c.post("/upload", files={"file": (name, data)}, headers=HTML, follow_redirects=False, **kw)


def test_plain_user_uploads_and_sees_only_their_own(staff):
    me, other = client([USER], sub="u1", name="Una"), client([USER], sub="u2", name="Otto")
    r = post(me, "mine.docx")
    assert r.status_code == 303 and r.headers["location"] == "/upload?notice=uploaded"
    post(other, "theirs.docx")
    page = me.get("/upload").text
    assert "mine.docx" in page and "theirs.docx" not in page and "Waiting in line" in page
    assert "File received" in me.get("/upload?notice=uploaded").text
    assert 'href="/upload"' in page  # the tab


def test_refusals_are_notices(staff):
    me = client([USER])
    assert post(me, "a.exe").headers["location"] == "/upload?notice=type"
    assert post(me, "a.docx", b"nope").headers["location"] == "/upload?notice=content"
    assert "Upload refused" in me.get("/upload?notice=type").text
    with db.connect() as c:
        c.execute("insert into uploads(user_key, source_id, s3_key, bytes, job_id) "
                  "select 'u1', %s, 'k', 1, 1 from generate_series(1, 20)", (staff,))
    assert post(me).headers["location"] == "/upload?notice=quota"
    assert "daily upload limit" in me.get("/upload?notice=quota").text


def test_post_without_a_staff_source_redirects_to_a_notice(staff):
    with db.connect() as c:
        c.execute("update sources set config = config - 'staff' where id=%s", (staff,))
    r = post(client([USER]))
    assert r.headers["location"] == "/upload?notice=off"
    assert "Uploads are not configured" in client([USER]).get("/upload?notice=off").text


def test_cross_origin_post_is_refused(staff):
    c = client([USER], origin="http://evil.example")
    assert post(c).status_code == 403


def test_no_staff_source_hides_the_tab_and_says_not_configured(staff):
    with db.connect() as c:
        c.execute("update sources set config = config - 'staff' where id=%s", (staff,))
    me = client([USER])
    page = me.get("/upload").text
    assert "not configured" in page and 'href="/upload"' not in page
    assert 'href="/upload"' not in me.get("/search").text


def test_review_page_shows_who_uploaded(make, staff):  # noqa: F811
    cid = make()
    with db.connect() as c:
        sid = c.execute("select d.source_id from cases c join documents d on d.id=c.document_id where c.id=%s", (cid,)).fetchone()[0]
        c.execute("insert into uploads(user_key, user_name, source_id, s3_key, bytes, job_id) values ('x-1','Una Uploader',%s,'k',1,1)", (sid,))
    assert "Uploaded by" in client(R).get(f"/review/{cid}").text and "Una Uploader" in client(R).get(f"/review/{cid}").text
    with db.connect() as c:
        c.execute("delete from uploads where user_key='x-1'")
    assert "Uploaded by" not in client(R).get(f"/review/{cid}").text


def test_the_tab_lookup_is_cached(staff, monkeypatch):
    from app import review
    assert review.staff_uploads() is True
    with db.connect() as c:
        c.execute("update sources set config = config - 'staff' where id=%s", (staff,))
    assert review.staff_uploads() is True  # still cached: no query
    review._staff_seen = (0.0, False)
    assert review.staff_uploads() is False


def test_page_shows_labels_and_loads_the_script_only_while_pending(staff):
    me = client([USER], sub="u1", name="Una")
    assert "upload.js" not in me.get("/upload").text and "updates every 15 seconds" not in me.get("/upload").text  # nothing yet
    post(me)
    page = me.get("/upload").text
    assert "Waiting" in page and "Waiting in line: " in page and 'class="dot"' in page
    assert "/static/upload.js?v=" in page and "updates every 15 seconds" in page and "Reload to see progress" in page
    assert "this page updates by itself" in me.get("/upload?notice=uploaded").text
    with db.connect() as c:
        c.execute("update jobs set status='failed' where id=(select job_id from uploads where user_key='u1')")
    page = me.get("/upload").text
    assert "Couldn&#39;t process" in page and "upload.js" not in page and "updates every 15 seconds" not in page


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_upload_js_does_not_reload_while_a_file_is_chosen(tmp_path):
    js = Path(__file__).parent.parent / "app/static/upload.js"
    stub = (f"var files=[], n=0, tick;"
            "global.document={getElementById:function(){return {files:files}}};"
            "global.location={reload:function(){n++}};"
            "global.setInterval=function(f,ms){tick=f; if(ms!==15000) throw 1};"
            f"require({str(js)!r});"
            "tick(); if(n!==1) throw new Error('should reload with no file');"
            "files.push({}); tick(); if(n!==1) throw new Error('reloaded with a file chosen');")
    subprocess.run(["node", "-e", stub], check=True)


def test_review_queue_counts_processing_documents_it_can_see(make, staff):  # noqa: F811
    from tests.test_review import DOCS
    rev = client(R)
    assert "still being processed" not in rev.get("/review").text  # hidden at 0
    mine, hidden = make(), make(acl=("g-other",))
    with db.connect() as c:
        for cid, st in ((mine, "running"), (hidden, "queued")):
            did = c.execute("select document_id from cases where id=%s", (cid,)).fetchone()[0]
            c.execute("insert into jobs(kind, status, payload) values ('extract', %s, jsonb_build_object('document_id', %s::bigint))", (st, did))
            c.execute("insert into jobs(kind, status, payload) values ('extract', 'done', jsonb_build_object('document_id', %s::bigint))", (did,))
        c.execute("update sources set acl_groups=%s where id=%s", ([DOCS], staff))
        j = c.execute("insert into jobs(kind, status) values ('ingest_upload','queued') returning id").fetchone()[0]
        c.execute("insert into uploads(user_key, user_name, source_id, s3_key, bytes, job_id) values ('x-1','Secret Name',%s,'up/k/Secret.docx',1,%s)", (staff, j))
    page = rev.get("/review").text
    assert "2 documents are still being processed and will appear here when ready." in page  # one extract + one upload; the other ACL is not counted
    assert "Secret" not in page and "Source doc" not in page.split("still being processed")[0].split("</h1>")[-1]
    with db.connect() as c:
        c.execute("delete from jobs where kind in ('extract','ingest_upload')")
