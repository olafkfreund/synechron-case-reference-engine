---
status: approved
issue: 159
author: olafkfreund
---

# Intent: unticking "contracts executed" stops every flagged extract

## Problem

When an admin unticks "contracts executed" on a source, `update` in
`app/sources.py` retires the flagged engagement cases (`RETIRE_FLAGGED`).
It also deletes the flagged extract jobs (`DROP_FLAGGED_JOBS`), but only
those still `queued`. Two kinds of job escape:

- **A job already running when the admin unticks.** `extract()` in
  `app/extract.py` never checks the flag. The job finishes and writes an
  `extracted` engagement case with reason "source marked executed".
- **A flagged job that had already failed.** The daily retry in
  `app/enqueue_crawls.py` (#120) requeues it, because the document has no
  case, and it then writes the case.

Either way, a contract the admin has just said is not executed becomes a
reviewable, approvable engagement case.

## Proposed outcome

- After an untick, no extract job queued because the source was marked
  executed writes a case. That covers jobs that are queued, running,
  failed and retried, or started later.
- Engagement cases that come from the contract itself being executed
  (reason "executed contract") are unaffected.
- A job that is stopped this way ends as `done`, not `failed`, so the daily
  retry does not pick it up again.

## Affected users and systems

- Admins who untick "contracts executed".
- Reviewers, who no longer see these cases.
- `app/extract.py` (`extract`), and possibly `app/sources.py`.

## Constraints

- The check must sit where the case is written, under a lock that orders it
  against the admin's save. `update` locks the source row `for update`.
- No model call is made for a job that will be thrown away, where that can
  be known up front.
- #132 adds a checksum check to the same write in `extract()`. Whichever
  lands second builds on the other.
- Re-ticking is out of scope; that is #160.

## Open questions

None.
