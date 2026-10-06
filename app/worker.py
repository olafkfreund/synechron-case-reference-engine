import signal
import sys
import time

from app import crawl, db, extract

MAX_ATTEMPTS = 3
HANDLERS = {
    "extract": lambda p: extract.extract(p["document_id"]),
    "crawl_s3": lambda p: crawl.crawl_s3(p["source_id"]),
}

# a running job this old belongs to a dead worker; first crawls can run for hours, and the
# daily schedule re-runs a crawl whose worker died, so crawls get a long window
CLAIM = """
update jobs set status='running', attempts=attempts+1, updated_at=now()
where id = (select id from jobs
            where status='queued' or (status='running' and updated_at < now() -
                  case when kind like 'crawl%%' then interval '6 hours' else interval '15 min' end)
            order by id limit 1 for update skip locked)
returning id, kind, payload, attempts
"""


def claim(conn):
    return conn.execute(CLAIM).fetchone()


def finish(job_id, attempts, status, error=None):
    # `attempts` guard: a worker whose job was reclaimed no longer owns it and must not
    # overwrite the newer worker's status
    with db.connect() as conn:
        conn.execute("update jobs set status=%s, error=%s, updated_at=now() where id=%s and attempts=%s",
                     (status, error, job_id, attempts))


def run_one() -> bool:
    """Claim and run one job. False when the queue is empty."""
    with db.connect() as conn:
        job = claim(conn)  # committed on exit, so other workers see 'running'
    if not job:
        return False
    job_id, kind, payload, attempts = job
    if attempts > MAX_ATTEMPTS:  # reclaimed after a worker died on its last attempt
        finish(job_id, attempts, "failed", "abandoned: worker died on the last attempt")
        return True
    if kind not in HANDLERS:
        finish(job_id, attempts, "failed", f"unknown job kind {kind!r}")
        return True
    try:
        HANDLERS[kind](payload)
    except Exception as e:
        # type + 200 chars: crawlers catch per-item errors themselves, llm.py strips input values
        status = "queued" if attempts < MAX_ATTEMPTS else "failed"
        finish(job_id, attempts, status, f"{type(e).__name__}: {str(e)[:200]}")
        print(f"job {job_id} {kind} {status}: {type(e).__name__}", flush=True)
    except BaseException:
        # SIGTERM (deploy) or Ctrl-C: give the attempt back and requeue now, not after the stale window
        with db.connect() as conn:
            conn.execute("update jobs set status='queued', attempts=attempts-1 where id=%s and attempts=%s",
                         (job_id, attempts))
        raise
    else:
        finish(job_id, attempts, "done")
    return True


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # PID 1 ignores SIGTERM by default
    db.init()
    while True:
        if not run_one():
            time.sleep(5)  # one job at a time per process; scale with more containers


if __name__ == "__main__":
    main()
