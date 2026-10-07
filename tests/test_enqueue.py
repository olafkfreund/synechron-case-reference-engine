import uuid

import pytest

from app import db, enqueue_crawls


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
