---
status: draft
issue: 60
spec: spec/2026-10-08-60-research-stale-window.md
---

# Plan: A slow research job is not run twice

## Approved decisions (self-contained)

- Measured worst case: about 75 min for a research job on the ECS worker
  (473 s per 40-page PDF at 2 CPUs, times 8, plus the network). Posted on
  #60.
- Fix: a heartbeat in `app/worker.py` `run_one()`, for every job kind. A
  daemon thread runs, every `HEARTBEAT = 60` s on its own connection:
  `update jobs set updated_at=now() where id=%s and attempts=%s and
  status='running'`.
  - A failed tick is caught; the next one tries again.
  - A `threading.Event`, set in a `finally` around the handler, stops the
    thread on every exit path.
- `CLAIM` is unchanged (crawls 6 h, everything else 15 min). No research time
  limit. No schema change.
- Accepted: a handler that hangs forever keeps its job until the worker
  restarts.
- The session model implements this itself: 1 step, 2 files.

## Steps

1. **Heartbeat.**
   - `app/worker.py`: `import threading`; `HEARTBEAT = 60` beside
     `MAX_ATTEMPTS`. Add `heartbeat(job_id, attempts, stop)`, which loops on
     `while not stop.wait(HEARTBEAT)` and runs the update above in
     `db.connect()`, inside `try/except Exception: pass` (with a
     `# noqa: BLE001` and a reason comment).
   - In `run_one()`, around `HANDLERS[kind](payload)` (line 54) only:
     `stop = threading.Event()`, then `threading.Thread(target=heartbeat,
     args=(job_id, attempts, stop), name="heartbeat", daemon=True).start()`.
     The existing `try/except/except BaseException/else` gets a
     `finally: stop.set()`. The early returns (abandoned, unknown kind) don't
     start a thread.
   - `tests/test_worker.py`:
     - `test_heartbeat_keeps_a_long_job_from_being_reclaimed`:
       monkeypatch `worker.HEARTBEAT` to 0.1. An `extract` handler backdates
       its own job to `now() - interval '20 min'`, sleeps 0.3 s, then asserts
       `worker.claim(c)` is `None` from a fresh connection. After
       `run_one()`, the row is `("done", 1)`. If the handler's assert fails,
       the job ends `queued`, so the test checks the final row, not only the
       assert inside the handler.
     - `test_heartbeat_stops_when_the_job_ends`: done and exception cases;
       afterwards no `threading.enumerate()` thread named `heartbeat` is
       alive (join with a short timeout first).

   → verify by:
   - the new long-job test fails with the heartbeat start commented out;
   - it passes with the change;
   - `docker compose build app && docker compose run --rm app pytest`.

   Traps:
   - compose has no bind mount: build before each run;
   - don't hold the claim connection open in the thread: each tick uses its
     own `db.connect()`;
   - keep the SIGTERM path (`except BaseException`) unchanged; `finally`
     runs after it.

## Tests

- The full suite is green. The new long-job test fails without the
  heartbeat.
- The existing stale-reclaim, last-attempt and SIGTERM tests pass unchanged.

## Rollback

- Revert the PR. No stored data changes.
