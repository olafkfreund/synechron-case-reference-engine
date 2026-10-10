import uuid

import pytest

from app import db, enqueue_crawls, ingest as ing
from tests.test_ingest import env  # noqa: F401


@pytest.fixture
def srcs():
    db.init()
    made = []

    def add(kind, enabled=True, job=None):
        with db.connect() as c:
            sid = c.execute("insert into sources(kind,name,enabled,config) values (%s,%s,%s,'{}') returning id",
                            (kind, uuid.uuid4().hex, enabled)).fetchone()[0]
            if job:
                c.execute("insert into jobs(kind, payload, status) values (%s, jsonb_build_object('source_id', %s::bigint), %s)",
                          (job[0], sid, job[1]))
        made.append(sid)
        return sid
    yield add
    with db.connect() as c:
        c.execute("delete from jobs where kind like 'crawl%%'")
        c.execute("delete from sources where id = any(%s)", (made,))


def jobs(sids):
    with db.connect() as c:
        return sorted(c.execute("select kind, (payload->>'source_id')::bigint, status from jobs where kind like 'crawl%%' "
                                "and (payload->>'source_id')::bigint = any(%s)", (sids,)).fetchall())


def test_one_job_per_enabled_source_and_no_duplicates(srcs):
    with db.connect() as c:  # other tests' leftovers must not matter
        c.execute("delete from jobs where kind like 'crawl%%'")
    s3, up, sp, off = srcs("s3"), srcs("upload"), srcs("sharepoint", enabled=False), srcs("confluence", enabled=False)
    busy = srcs("confluence", job=("crawl_confluence", "running"))
    waiting = srcs("sharepoint", job=("crawl_sharepoint", "queued"))
    finished = srcs("s3", job=("crawl_s3", "done"))
    enqueue_crawls.main()
    got = jobs([s3, up, sp, off, busy, waiting, finished])
    assert got == sorted([("crawl_s3", s3, "queued"), ("crawl_s3", up, "queued"),
                          ("crawl_confluence", busy, "running"), ("crawl_sharepoint", waiting, "queued"),
                          ("crawl_s3", finished, "done"), ("crawl_s3", finished, "queued")])
    enqueue_crawls.main()  # running it twice does not pile up jobs
    assert len(jobs([s3, up, finished])) == 4


def _extracts(did):
    with db.connect() as c:
        return c.execute("select status, payload from jobs where kind='extract' and (payload->>'document_id')::bigint=%s "
                         "order by id", (did,)).fetchall()


def _run_main_only_for(sid):
    """main() also touches other tests' rows: undo everything it added except this source's extract retries."""
    with db.connect() as c:
        top = c.execute("select coalesce(max(id), 0) from jobs").fetchone()[0]
    enqueue_crawls.main()
    with db.connect() as c:
        c.execute("delete from jobs where id > %s and not (kind = 'extract' and (payload->>'document_id')::bigint in "
                  "(select id from documents where source_id=%s))", (top, sid))


def _failed_doc(sid):
    ing.ingest(sid, "in/a.docx", "a.docx", b"one")
    with db.connect() as c:
        did = c.execute("select id from documents where source_id=%s", (sid,)).fetchone()[0]
        c.execute("update jobs set status='failed' where kind='extract' and (payload->>'document_id')::bigint=%s", (did,))
    return did


def test_failed_extract_is_requeued_once(env):
    _, sid = env
    did = _failed_doc(sid)
    failed_payload = _extracts(did)[0][1]
    _run_main_only_for(sid)
    assert _extracts(did) == [("failed", failed_payload), ("queued", failed_payload)]
    _run_main_only_for(sid)  # the latest job is queued now: no second one
    assert len(_extracts(did)) == 2


@pytest.mark.parametrize("variant", ["case", "deleted", "done"])
def test_no_requeue_with_case_deleted_or_done(env, variant):
    _, sid = env
    did = _failed_doc(sid)
    with db.connect() as c:
        if variant == "case":
            c.execute("insert into cases(document_id, status) values (%s, 'rejected')", (did,))
        elif variant == "deleted":
            c.execute("update documents set deleted_at=now() where id=%s", (did,))
        else:
            c.execute("update jobs set status='done' where kind='extract' and (payload->>'document_id')::bigint=%s", (did,))
    _run_main_only_for(sid)
    assert len(_extracts(did)) == 1
