---
status: approved
issue: 144
author: olafkfreund
---

# Intent: Approving a merged case can race a crawl and stay approved on a member's retired version

## Problem

A merged engagement M has member cases A and B, each from its own document.
When a member's document changes or is retired, `reopen_merged`
(`app/ingest.py:69-73`) sends M back to review. It updates only rows with
`status = 'approved'`.

Suppose a reviewer is approving M at the same moment:

- `approve` holds M's row lock, but M is still `extracted` in every other
  transaction's snapshot.
- Meanwhile a crawl ingests a new version of A and runs `reopen_merged`.
- Postgres (READ COMMITTED) skips M, because it doesn't match the WHERE clause
  in its snapshot, and never waits for the lock.
- Approve then commits M as approved, checked against A's old text, for 12
  months.

Search and outputs check only that the member documents are live and
visible, not the members' status. So a stale or retired contract ships.
Unticking **contracts executed** during a merged approve hits the same gap.

Spec #55 says member texts are read under a row lock. The code reads them
without one.

## Proposed outcome

A member change that lands while M is being approved still sends M back to
review, whichever transaction commits first.

## Affected users and systems

- `app/ingest.py` (`reopen_merged`), which both ingest and the
  contracts-executed toggle call.
- `app/review.py` (`load`, `approve`), if the lock goes there instead.
- Tests: `tests/test_ingest.py` and `tests/test_review.py`.

## Constraints

- Fail safe: when unsure, M goes back to review. It is never left approved.
- No new deadlock between approve and ingest.

## Open questions

1. **Where does the fix go?**
   - **A. `reopen_merged` matches every non-rejected merged case**
     (`where status <> 'rejected'`). The update then sees M, waits for
     approve's lock, re-checks the row and sets it back to `extracted`. It is
     one line, and both callers get it.
   - **B. `load()` reads the member rows `for share`**, as the spec
     describes. Ingest's member update then waits for the approve to finish.
     This changes the lock order between approve and ingest.

   **Recommendation: A.** It is the smallest change, with no new lock order.
   Reopening an already `extracted` case is a no-op.

**Decision (approved by olafkfreund, 2026-10-10): A.**
