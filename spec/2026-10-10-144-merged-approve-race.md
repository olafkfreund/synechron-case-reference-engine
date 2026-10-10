---
status: approved
issue: 144
intent: intent/2026-10-10-144-merged-approve-race.md
---

# Spec: A member change during a merged approve still reopens the merged case

## Design

Approved decision A. In `app/ingest.py` `reopen_merged`, change
`where status = 'approved'` to `where status <> 'rejected'`:

```python
conn.execute("update cases set status = 'extracted' where status <> 'rejected' and id in "
             "(select merged_into from cases where document_id = any(%s) and merged_into is not null)",
             (doc_ids,))
```

**Why this closes the race.**

1. `approve` holds merged case M's row lock: `load()` reads with
   `for update of c`.
2. While it does, M is `extracted` in every other snapshot. Today's
   `status = 'approved'` filter rejects M without waiting, so the update never
   reaches it.
3. With `<> 'rejected'`, the update matches M and blocks on the lock.
4. When approve commits, Postgres (READ COMMITTED) re-evaluates the WHERE
   clause on the new row version. The status is now `approved`, which still
   matches, so M is set back to `extracted`.
5. If approve rolls back instead, M is still `extracted`, and the update is a
   no-op.

Either way, M ends up in review, never approved.

**When ingest commits first.**

1. Ingest locks M through the update.
2. Approve's `for update` waits for it.
3. Approve then re-reads M (still `extracted`, same `VERSION`, since `data`
   is untouched), so it proceeds.
4. Its later member queries are new statements, so they see the new member
   text and status:
   - the rejected-member guard returns 409 for a retired member;
   - `check()` runs against the new text.

This is today's behaviour for that order.

**Callers.** Both callers get the fix with no change of their own:

- `ingest()`, at `app/ingest.py:118`;
- the contracts-executed untick in `app/sources.py` `update`, at line 122.

**No new deadlock.**

- Ingest locks the member row (its `update cases ... where document_id`) and
  then M.
- Approve locks only M. Its member reads are plain selects, which take no row
  lock.

So the two never wait on each other in opposite order. The source untick
locks members through `RETIRE_FLAGGED`, then M: the same order.

**Not changed.**

- `approved_by`, `approved_at` and `review_due` are left as they are today
  when the case is reopened. Search requires `status='approved'`, so the
  stale values are inert.
- Spec #55's claim about a row lock on member texts is not implemented. Fixing
  it was option B, which was rejected.

## Alternatives rejected

- **B. `load()` reads the members `for share`:** this adds a lock between
  approve and ingest, in a new order. The approver chose A.
- **Re-checking member checksums in `approve`:** that is a second mechanism
  for the same race, and it still misses an ingest that commits after the
  check.

## Risks

- **Extra writes.** Every member change now also writes a no-op update on an
  already-`extracted` M: one dead tuple per member change. This is negligible.
- **Brief waits.** Ingest briefly waits on a reviewer's approve of M. Approve is
  short and makes no model call, so the wait is milliseconds.
- **A rejected merged case** is not touched. This is unreachable anyway:
  `reject` refuses merged cases with "un-merge instead", and `unmerge` clears
  `merged_into` on the members.

## Verification

New test in `tests/test_ingest.py`, which fails on main:

`test_member_change_during_merged_approve_reopens_it`:

1. Set up with `merged_pair(sid)`, then set M to `extracted`.
2. A first connection runs `select 1 from cases where id=%s for update` on M,
   standing in for approve.
3. A thread runs `ing.ingest(sid, "a", "a", b"two")`.
4. `_wait_for_lock_wait(first)`. On main, ingest never waits, so this raises
   "ingest never waited…". That is the failure.
5. `first.execute("update cases set status='approved' where id=%s")`, then
   `commit`.
6. Join the thread, then assert `status(M) == "extracted"`.

Existing tests pass unchanged:

- `test_changed_member_reopens_its_approved_merged_case`
- `test_member_that_is_no_longer_an_engagement_is_rejected_and_blocks_approval`
- `test_unrelated_document_leaves_merged_cases_alone`
- the `tests/test_sources.py` untick test

Run the full suite with
`docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`. It must be green.
