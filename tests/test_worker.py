import threading
import time

import pytest

from app import db, worker


@pytest.fixture(autouse=True)
def clean():
    db.init()
    with db.connect() as c:
        c.execute("delete from jobs")
    yield
    with db.connect() as c:
        c.execute("delete from jobs")


def add(kind="t", n=1):
    with db.connect() as c:
        for _ in range(n):
            c.execute("insert into jobs(kind) values (%s)", (kind,))


def row():
    with db.connect() as c:
        return c.execute("select status, attempts, error from jobs order by id").fetchall()


def test_concurrent_claims_never_overlap():
    add(n=2)
    a, b = db.connect(), db.connect()
    try:
        ja = worker.claim(a)  # a's transaction is still open, its row is locked
        jb = worker.claim(b)
        assert ja[0] != jb[0]
        assert worker.claim(b) is None
    finally:
        a.close()
        b.close()


def test_stale_running_job_reclaimed_fresh_one_not():
    add(n=2)
    with db.connect() as c:
        c.execute("update jobs set status='running', updated_at=now() - interval '20 min' "
                  "where id=(select min(id) from jobs)")
        c.execute("update jobs set status='running' where id=(select max(id) from jobs)")
    with db.connect() as c:
        assert worker.claim(c) is not None
        assert worker.claim(c) is None


def test_success_done(monkeypatch):
    add()
    monkeypatch.setitem(worker.HANDLERS, "t", lambda p: None)
    assert worker.run_one() is True
    assert row() == [("done", 1, None)]
    assert worker.run_one() is False


def test_failure_requeues_then_fails_after_3(monkeypatch):
    add()

    def boom(p):
        raise RuntimeError("x" * 500)
    monkeypatch.setitem(worker.HANDLERS, "t", boom)
    worker.run_one()
    worker.run_one()
    assert row()[0][:2] == ("queued", 2)
    worker.run_one()
    status, attempts, error = row()[0]
    assert (status, attempts) == ("failed", 3)
    assert error.startswith("RuntimeError: x") and len(error) == len("RuntimeError: ") + 200
    assert worker.run_one() is False


def test_policy_error_fails_without_retry(monkeypatch):
    add()

    def refused(p):
        raise worker.llm.PolicyError("EXTRACT_MODEL may not read confidential data")
    monkeypatch.setitem(worker.HANDLERS, "t", refused)
    worker.run_one()
    assert row() == [("failed", 1, "PolicyError: EXTRACT_MODEL may not read confidential data")]


def test_unknown_kind_fails_clearly():
    add("nope")
    worker.run_one()  # no retries: it can never succeed
    assert "unknown job kind 'nope'" in row()[0][2] and row()[0][0] == "failed"


def test_dead_worker_on_last_attempt_is_abandoned():
    add()
    with db.connect() as c:
        c.execute("update jobs set status='running', attempts=3, updated_at=now() - interval '20 min'")
    worker.run_one()
    assert row()[0][0] == "failed" and "abandoned" in row()[0][2]


def test_late_worker_cannot_overwrite_newer_status():
    add()
    with db.connect() as c:
        job_id, *_ = worker.claim(c)  # worker A: attempts=1
    with db.connect() as c:  # worker B reclaimed it (attempts=2) and finished it
        c.execute("update jobs set attempts=2, status='done' where id=%s", (job_id,))
    worker.finish(job_id, 1, "queued", "A failed late")
    assert row()[0][:2] == ("done", 2)


def test_long_crawl_not_reclaimed_but_stale_extract_is():
    add("crawl_s3")
    add("extract")
    with db.connect() as c:
        c.execute("update jobs set status='running', attempts=1, updated_at=now() - interval '20 min'")
        got = worker.claim(c)
    assert got[1] == "extract"


def test_sigterm_requeues_and_gives_attempt_back(monkeypatch):
    add("extract")
    def stop(p):
        raise SystemExit(0)
    monkeypatch.setitem(worker.HANDLERS, "extract", stop)
    with pytest.raises(SystemExit):
        worker.run_one()
    assert row()[0][:2] == ("queued", 0)


def test_heartbeat_keeps_a_long_job_from_being_reclaimed(monkeypatch):
    monkeypatch.setattr(worker, "HEARTBEAT", 0.1)
    add("extract")
    seen = []
    def slow(p):
        with db.connect() as c:
            c.execute("update jobs set updated_at=now() - interval '20 min'")
        time.sleep(0.5)  # 5 ticks of headroom on a loaded runner
        with db.connect() as c:
            seen.append(worker.claim(c))
    monkeypatch.setitem(worker.HANDLERS, "extract", slow)
    worker.run_one()
    assert seen == [None]
    assert row()[0][:2] == ("done", 1)


def heartbeats():
    return [t for t in threading.enumerate() if t.name == "heartbeat" and t.is_alive()]


@pytest.mark.parametrize("fail", [False, True])
def test_heartbeat_stops_when_the_job_ends(monkeypatch, fail):
    monkeypatch.setattr(worker, "HEARTBEAT", 0.05)
    add("extract")
    def handler(p):
        if fail:
            raise ValueError("boom")
    monkeypatch.setitem(worker.HANDLERS, "extract", handler)
    worker.run_one()
    for t in heartbeats():
        t.join(1)
    assert heartbeats() == []
