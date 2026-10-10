---
status: draft
issue: 144
spec: spec/2026-10-10-144-merged-approve-race.md
---

# Plan: A member change during a merged approve still reopens the merged case

Approved decision A: `reopen_merged` in `app/ingest.py` matches every merged
case that is not rejected (`status <> 'rejected'`), instead of only
`status = 'approved'`.

Why it works:

- While `approve` holds merged case M's row lock, M looks `extracted` to
  others. Today's filter skips M without waiting, so the reopen is lost.
- With the new filter, the update waits on M's lock. After approve commits,
  READ COMMITTED re-checks the new row (`approved`, still matching) and sets
  it back to `extracted`. If approve rolls back, the update is a no-op.
- Lock order stays member, then merged case, for both callers: `ingest()`
  (`app/ingest.py:118`) and the contracts-executed untick in `app/sources.py`
  `update` (line 122). No caller changes.
- `approved_by`, `approved_at` and `review_due` are left as they are on
  reopen; search needs `status='approved'`, so they are inert.
- Rejected merged cases are untouched (unreachable: `reject` refuses merged
  cases).

Two steps, two files: implement it yourself, no coder handoff.

## Steps

1. `tests/test_ingest.py`, after
   `test_unrelated_document_leaves_merged_cases_alone` (around line 369): add
   `test_member_change_during_merged_approve_reopens_it(env)`. It is placed
   above `_wait_for_lock_wait` (line 378), which is fine because the helper is
   resolved at call time.
   ```python
   def test_member_change_during_merged_approve_reopens_it(env):
       import threading
       _, sid = env
       new, _ = merged_pair(sid)
       with db.connect() as c:
           c.execute("update cases set status='extracted' where id=%s", (new,))
       first = db.connect()  # stands in for approve: load() holds M's row lock
       first.execute("select 1 from cases where id=%s for update", (new,))
       t = threading.Thread(target=ing.ingest, args=(sid, "a", "a", b"two"))
       t.start()
       try:
           _wait_for_lock_wait(first)
           first.execute("update cases set status='approved' where id=%s", (new,))
           first.commit()
       finally:
           first.close()  # releases the lock (rolls back on failure), or teardown blocks on it
       t.join(10)
       assert status(new) == "extracted"
   ```
   Verify it fails on main:
   `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_ingest.py::test_member_change_during_merged_approve_reopens_it`.
   It should fail with "ingest never waited for the save's lock".
   Traps:
   - There is no bind mount, so build before every run.
   - Never run `docker compose up` or `down`.
   - The `finally: first.close()` is required, or teardown hangs on the held
     lock.

2. `app/ingest.py:71`, in `reopen_merged`: change `where status = 'approved'`
   to `where status <> 'rejected'`. Nothing else changes, including the
   docstring.
   Verify by running the step 1 command, which now passes, then
   `docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_ingest.py tests/test_sources.py tests/test_review.py`.
   Traps: only the WHERE clause changes, and the `id in (select merged_into ...)`
   subquery stays exactly as it is.

## Tests

- The new test fails on main and passes on the branch.
- These existing tests pass unchanged:
  - `test_changed_member_reopens_its_approved_merged_case`
  - `test_member_that_is_no_longer_an_engagement_is_rejected_and_blocks_approval`
  - `test_unrelated_document_leaves_merged_cases_alone`
  - the untick test in `tests/test_sources.py`
- The full suite is green:
  `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`.

## Rollback

Revert the commit. Restoring `status = 'approved'` brings back the race, but
nothing else depends on it. There is no schema or data change.
