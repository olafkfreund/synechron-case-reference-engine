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


def test_burst_never_exceeds_the_quota_and_busy_ones_get_429(staff, monkeypatch):
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
    assert set(codes) <= {200, 429} and 1 <= codes.count(200) <= 3 and used(who)[0] == codes.count(200)


def test_a_busy_user_gets_429_without_waiting(staff):
    _, who = staff
    with db.connect() as holder:
        holder.execute("select pg_advisory_xact_lock(hashtext('upload:'||%s))", (who.sub,))
        t = time.monotonic()
        with pytest.raises(HTTPException) as e:
            accept(who)
    assert e.value.status_code == 429 and "in progress" in e.value.detail and time.monotonic() - t < 2
    accept(who)  # the lock is gone with the holder's transaction


def test_a_failed_insert_deletes_the_stored_object(staff):
    _, who = staff
    bad = SimpleNamespace(sub=who.sub, name=object())  # psycopg cannot adapt it: the uploads insert raises after put_object
    with pytest.raises(Exception):  # noqa: B017
        accept(bad)
    assert boto3.client("s3").list_objects_v2(Bucket="src", Prefix="up/").get("KeyCount") == 0


def test_upload_conversion_is_page_capped_and_killed_on_timeout(env, monkeypatch):
    s3, sid = env
    key = f"in/{uuid.uuid4().hex}/a.docx"
    s3.put_object(Bucket="src", Key=key, Body=b"hello")
    monkeypatch.setenv("UPLOAD_MAX_PAGES", "7")
    monkeypatch.setattr(ing, "to_markdown", lambda data, name, max_pages=None: data.decode() if max_pages == 7 else 1 / 0)
    assert ing.ingest_upload(sid, key) == "new"
    monkeypatch.setenv("UPLOAD_CONVERT_TIMEOUT", "1")
    monkeypatch.setattr(ing, "to_markdown", lambda data, name, max_pages=None: time.sleep(60))
    key2 = f"in/{uuid.uuid4().hex}/b.docx"
    s3.put_object(Bucket="src", Key=key2, Body=b"other")
    with pytest.raises(ing.PolicyError):  # the worker does not retry a policy error
        ing.ingest_upload(sid, key2)
    with db.connect() as c:
        c.execute("delete from documents where source_id=%s", (sid,))


def test_status_codes_and_owner_only(staff):
    sid, who = staff
    uid = accept(who)
    other = SimpleNamespace(sub="someone-else", name="o")

    def status(u=who):
        with db.connect() as c:
            return ing.upload_status(c, u, uid)[0]
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
        assert ing.upload_status(c, who, uid)[0] == "queued"


def test_failed_extraction_and_merged_cases(staff):
    sid, who = staff
    uid = accept(who)
    with db.connect() as c:
        key, job = c.execute("select s3_key, job_id from uploads where id=%s", (uid,)).fetchone()
        c.execute("update jobs set status='done' where id=%s", (job,))
        doc = c.execute("insert into documents(source_id, external_id, checksum) values (%s,%s,'x') returning id", (sid, key)).fetchone()[0]
        ext = c.execute("insert into jobs(kind, status, payload) values ('extract','failed', jsonb_build_object('document_id', %s::bigint)) returning id", (doc,)).fetchone()[0]

    def status():
        with db.connect() as c:
            return ing.upload_status(c, who, uid)[0]
    assert status() == "failed"
    with db.connect() as c:
        c.execute("delete from jobs where id=%s", (ext,))
        other = c.execute("insert into documents(source_id, external_id, checksum) values (%s,%s,'y') returning id", (sid, key + "2")).fetchone()[0]
        parent = c.execute("insert into cases(document_id, status) values (%s,'approved') returning id", (other,)).fetchone()[0]
        c.execute("insert into cases(document_id, status, merged_into) values (%s,'extracted',%s)", (doc, parent))
    assert status() == "approved"  # the merged parent decides
    with db.connect() as c:
        c.execute("update cases set status='rejected' where id=%s", (parent,))
    assert status() == "rejected"


def test_upload_text_is_kept_in_full(env):
    s3, sid = env
    key = f"in/{uuid.uuid4().hex}/a.docx"
    s3.put_object(Bucket="src", Key=key, Body=b"x" * 30_000)
    ing.ingest_upload(sid, key)
    with db.connect() as c:
        assert c.execute("select length(text) from documents where source_id=%s and external_id=%s", (sid, key)).fetchone()[0] == 30_000
        c.execute("delete from documents where source_id=%s", (sid,))


def test_stages_position_and_estimate(staff):
    sid, who = staff
    with db.connect() as c:  # a job queued before the upload is ahead of it; one queued after is not
        old = c.execute("insert into jobs(kind, status) values ('x','queued') returning id").fetchone()[0]
        n_before = c.execute("select count(*) from jobs where status='queued'").fetchone()[0]
    uid = accept(who)
    with db.connect() as c:
        key, job = c.execute("select s3_key, job_id from uploads where id=%s", (uid,)).fetchone()
        c.execute("insert into jobs(kind, status) values ('x','queued'), ('x','queued')")

    def st():
        with db.connect() as c:
            return ing.upload_status(c, who, uid)
    try:
        assert st() == ("queued", "Waiting", f"Waiting in line: {n_before} ahead of yours.")
        with db.connect() as c:
            c.execute("update jobs set status='running' where id=%s", (job,))
        assert st()[:2] == ("reading", "Reading the document")
        with db.connect() as c:
            c.execute("update jobs set status='done' where id=%s", (job,))
            doc = c.execute("insert into documents(source_id, external_id, checksum) values (%s,%s,'x') returning id", (sid, key)).fetchone()[0]
            ext = c.execute("insert into jobs(kind, status, payload) values ('extract','running', jsonb_build_object('document_id', %s::bigint)) returning id",
                            (doc,)).fetchone()[0]
            c.execute("delete from jobs where kind='extract' and status='done'")  # a clean history for the estimate
        assert st() == ("writing", "Writing the case", "The model is drafting the case: usually a few minutes.")  # < 5 finished
        with db.connect() as c:
            for i in range(20):  # run 2 and 6 minutes, alternating, after a 30-minute wait in the queue: the quartiles are 2 and 6
                c.execute("insert into jobs(kind, status, created_at, started_at, updated_at) values ('extract','done', "
                          "now() - interval '40 minutes', now() - interval '10 minutes', now() - interval '10 minutes' + %s * interval '1 minute')",
                          (2 if i % 2 else 6,))
            c.execute("insert into jobs(kind, status, created_at, updated_at) values ('extract','done', now() - interval '90 minutes', now())")  # no start: ignored
        assert st()[2] == "The model is drafting the case: about 2\u20136 minutes."
        with db.connect() as c:
            c.execute("update jobs set status='queued' where id=%s", (ext,))
        assert st()[:2] == ("queued", "Waiting")
    finally:
        with db.connect() as c:
            c.execute("delete from jobs where kind in ('x','extract')")


def test_reading_goes_stale_and_a_vanished_job_does_not_hide_the_document(staff):
    sid, who = staff
    uid = accept(who)
    with db.connect() as c:
        key, job = c.execute("select s3_key, job_id from uploads where id=%s", (uid,)).fetchone()
        c.execute("update jobs set status='running', started_at=now() where id=%s", (job,))

    def st():
        with db.connect() as c:
            return ing.upload_status(c, who, uid)
    assert st()[2] == "Converting the file. Usually under a minute."
    with db.connect() as c:
        c.execute("update jobs set started_at=now() - interval '10 minutes' where id=%s", (job,))
    assert st()[2] == "Converting the file. This is taking longer than usual."
    with db.connect() as c:  # the document exists but the ingest job row is gone: the case decides
        doc = c.execute("insert into documents(source_id, external_id, checksum) values (%s,%s,'x') returning id", (sid, key)).fetchone()[0]
        c.execute("insert into cases(document_id, status) values (%s,'extracted')", (doc,))
        c.execute("delete from jobs where id=%s", (job,))
    assert st()[0] == "in_review"


def test_started_at_is_set_on_the_first_claim_only(staff):
    from app import worker
    accept(staff[1])
    with db.connect() as c:
        c.execute("update jobs set status='done' where status in ('queued','running')")
        j = c.execute("insert into jobs(kind) values ('x') returning id").fetchone()[0]
    with db.connect() as c:  # separate transactions: now() is fixed inside one
        assert worker.claim(c)[0] == j
    with db.connect() as c:
        first = c.execute("select started_at from jobs where id=%s", (j,)).fetchone()[0]
        assert first is not None
        c.execute("update jobs set status='queued' where id=%s", (j,))
    with db.connect() as c:
        assert worker.claim(c)[0] == j
    with db.connect() as c:
        assert c.execute("select started_at from jobs where id=%s", (j,)).fetchone()[0] == first
        c.execute("delete from jobs where id=%s", (j,))


def test_capability_cases_say_so(staff):
    sid, who = staff
    uid = accept(who)
    with db.connect() as c:
        key, job = c.execute("select s3_key, job_id from uploads where id=%s", (uid,)).fetchone()
        c.execute("update jobs set status='done' where id=%s", (job,))
        doc = c.execute("insert into documents(source_id, external_id, checksum) values (%s,%s,'x') returning id", (sid, key)).fetchone()[0]
        case = c.execute("insert into cases(document_id, status, basis) values (%s,'extracted','capability') returning id", (doc,)).fetchone()[0]

    def st():
        with db.connect() as c:
            return ing.upload_status(c, who, uid)
    assert st() == ("in_review", "In review", "A reviewer will check it; it will be shown as a capability document (not delivery evidence).")
    with db.connect() as c:
        c.execute("update cases set status='approved' where id=%s", (case,))
    assert st()[2] == "Approved as a capability document: listed under Capability in search."
    with db.connect() as c:
        c.execute("update cases set basis='delivered' where id=%s", (case,))
    assert st()[2] == "Visible to staff in search."
    with db.connect() as c:
        c.execute("delete from cases where id=%s", (case,))
    assert st()[2] == "Stored, but not a case study or capability document."
