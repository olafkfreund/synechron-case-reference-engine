import uuid

import boto3
import psycopg
import pytest

from app import db, ingest as ing, worker
from tests.test_hardening import app_role, as_app  # noqa: F401 - fixture
from tests.test_ingest import env  # noqa: F401 - fixture


def test_upload_job_ingests_the_object(env):
    s3, sid = env
    key = f"in/{uuid.uuid4().hex}/a.docx"
    s3.put_object(Bucket="src", Key=key, Body=b"hello upload")
    worker.HANDLERS["ingest_upload"]({"source_id": sid, "key": key})
    with db.connect() as c:
        assert c.execute("select title from documents where source_id=%s and external_id=%s", (sid, key)).fetchall() == [("a.docx",)]
        c.execute("delete from documents where source_id=%s", (sid,))


def test_uploads_are_append_only_for_the_app(app_role):  # noqa: F811
    with db.connect() as c:
        db.apply_app_role(c, "pw-1")
    for stmt in ("update uploads set bytes=1", "delete from uploads"):
        with as_app("pw-1") as a:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                a.execute(stmt)
    with as_app("pw-1") as a:
        a.execute("insert into uploads (user_key, source_id, s3_key, bytes, job_id) values ('t-up', 1, 'k', 1, 1)")
    with db.connect() as c:
        c.execute("delete from uploads where user_key='t-up'")


# ---- accept_upload and upload_status (step 2) ----
import threading
import time
from types import SimpleNamespace

from fastapi import HTTPException

DOCX = b"PK\x03\x04body"


@pytest.fixture
def staff(env):
    s3, _ = env
    with db.connect() as c:
        sid = c.execute("insert into sources(kind,name,config,acl_groups) values ('upload',%s,%s,%s) returning id",
                        (uuid.uuid4().hex, '{"bucket":"src","prefix":"up/","staff":true}', ["staff"])).fetchone()[0]
    who = SimpleNamespace(sub=f"u-{uuid.uuid4().hex}", name="Una")
    yield sid, who
    with db.connect() as c:
        c.execute("delete from jobs where payload->>'source_id' = %s", (str(sid),))
        c.execute("delete from uploads where source_id=%s", (sid,))
        c.execute("delete from sources where id=%s", (sid,))


def accept(who, data=DOCX, name="a.docx"):
    with db.connect() as c:
        return ing.accept_upload(c, who, name, data)


def used(who):
    with db.connect() as c:
        return ing.upload_used(c, who.sub)


def test_accept_stores_one_job_and_one_row(staff):
    sid, who = staff
    uid = accept(who)
    with db.connect() as c:
        key, job = c.execute("select s3_key, job_id from uploads where id=%s", (uid,)).fetchone()
        assert c.execute("select kind, payload from jobs where id=%s", (job,)).fetchone() == (
            "ingest_upload", {"source_id": sid, "key": key})
        assert c.execute("select count(*) from jobs where payload->>'source_id'=%s", (str(sid),)).fetchone()[0] == 1
    assert boto3.client("s3").get_object(Bucket="src", Key=key)["Body"].read() == DOCX


def test_no_staff_source_refuses(env):
    with db.connect() as c:
        c.execute("update sources set enabled=false where kind='upload'")
    try:
        with pytest.raises(HTTPException) as e:
            accept(SimpleNamespace(sub="u-none", name="x"))
        assert e.value.status_code == 503
    finally:
        with db.connect() as c:
            c.execute("update sources set enabled=true where kind='upload'")


def test_bad_file_is_refused_before_anything_is_stored(staff):
    _, who = staff
    for name, data in (("a.exe", DOCX), ("a.docx", b"nope")):
        with pytest.raises(HTTPException) as e:
            accept(who, data, name)
        assert e.value.status_code == 400
    assert used(who) == (0, 0)


def test_quota_by_count(staff):
    _, who = staff
    for _ in range(20):
        accept(who)
    with pytest.raises(HTTPException) as e:
        accept(who)
    assert e.value.status_code == 429 and "20 files" in e.value.detail
    assert used(who)[0] == 20


def test_quota_by_bytes(staff, monkeypatch):
    _, who = staff
    monkeypatch.setattr(ing, "UPLOAD_QUOTA", (20, 2 * len(DOCX)))
    accept(who)
    accept(who)
    with pytest.raises(HTTPException) as e:
        accept(who)
    assert e.value.status_code == 429


def test_quota_ignores_old_rows(staff):
    _, who = staff
    for _ in range(20):
        accept(who)
    with db.connect() as c:
        c.execute("update uploads set created_at = now() - interval '25 hours' where user_key=%s", (who.sub,))
    accept(who)


def test_burst_stays_within_the_quota(staff, monkeypatch):
    _, who = staff
    monkeypatch.setattr(ing, "UPLOAD_QUOTA", (3, 10**9))
    real = ing.upload_used
    monkeypatch.setattr(ing, "upload_used", lambda *a: (r := real(*a), time.sleep(0.3))[0])  # widen the race window
    codes = []

    def go():
        try:
            accept(who)
            codes.append(200)
        except HTTPException as e:
            codes.append(e.status_code)
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes) == [200] * 3 + [429] * 3 and used(who)[0] == 3


def test_status_codes_and_owner_only(staff):
    sid, who = staff
    uid = accept(who)
    other = SimpleNamespace(sub="someone-else", name="o")

    def status(u=who):
        with db.connect() as c:
            return ing.upload_status(c, u, uid)
    with db.connect() as c:
        key, job = c.execute("select s3_key, job_id from uploads where id=%s", (uid,)).fetchone()

    def set_job(st):
        with db.connect() as c:
            c.execute("update jobs set status=%s where id=%s", (st, job))
    assert status() == "queued"
    set_job("failed")
    assert status() == "failed"
    set_job("done")
    assert status() == "failed"  # done without a document
    with db.connect() as c:
        doc = c.execute("insert into documents(source_id, external_id, checksum) values (%s,%s,'x') returning id",
                        (sid, key)).fetchone()[0]
    with db.connect() as c:
        c.execute("insert into jobs(kind, payload) values ('extract', jsonb_build_object('document_id', %s::bigint))", (doc,))
    assert status() == "queued"  # extraction still pending
    with db.connect() as c:
        c.execute("delete from jobs where kind='extract' and (payload->>'document_id')::bigint=%s", (doc,))
    assert status() == "indexed"
    for case, want in (("extracted", "in_review"), ("approved", "approved"), ("rejected", "rejected")):
        with db.connect() as c:
            c.execute("delete from cases where document_id=%s", (doc,))
            c.execute("insert into cases(document_id, status) values (%s,%s)", (doc, case))
        assert status() == want
    with pytest.raises(HTTPException) as e:
        status(other)
    assert e.value.status_code == 404


def test_status_survives_a_deleted_job(staff):
    _, who = staff
    uid = accept(who)
    with db.connect() as c:
        c.execute("delete from jobs where id=(select job_id from uploads where id=%s)", (uid,))
        assert ing.upload_status(c, who, uid) == "queued"
