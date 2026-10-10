---
status: draft
issue: 132
author: olafkfreund
---

# Intent: A queued extract job revives a document that a newer version retired

## Problem

`ingest()` queues an extract job for each version it triages as a case. The
job's payload carries the basis (`delivered` or `engagement`). Since #120 it
also carries the document's checksum.

When a newer version arrives that is no longer a case, `ingest()`
(`app/ingest.py:113-123`) does two things:

- it sets any existing case to `rejected`;
- it queues nothing.

It doesn't touch an earlier extract job that is still queued or running.
That job later runs `extract()` (`app/extract.py:141-155`). `extract()` reads
the document's current text, which is now the new version, and upserts a case
as `extracted` with the old basis. A reviewer can then approve a document the
engine has just ruled out.

The queue backs up after a first crawl, because one worker takes jobs in order.
So this is likely in normal use.

## Proposed outcome

An extract job only ever produces a case for the version it was queued for. A
job that is overtaken by a newer version does nothing.

## Affected users and systems

- `app/extract.py` (`extract`) and `app/worker.py` (the extract handler), or
  `app/ingest.py` (the write transaction).
- Reviewers, who would otherwise see revived cases.
- Tests: `tests/test_ingest.py` and `tests/test_extract.py`.

## Constraints

- Fail safe: when in doubt, don't create or reopen a case.
- No schema change.
- Jobs queued before #120 have no checksum. They must keep working as today.

## Open questions

1. **Where is the stale job stopped?**
   - **A. `ingest()` deletes the document's queued extract jobs** in its write
     transaction, before the optional insert, the way `DROP_FLAGGED_JOBS`
     does. This misses a job that is already running.
   - **B. `extract()` checks the job's checksum against the document's
     current one** and does nothing when they differ. The worker passes the
     checksum from the payload, and a job with no checksum runs as today. This
     covers queued and running jobs alike, as one check in the place that
     writes the case.

   **Recommendation: B.** It covers the running case too, and it uses the
   checksum #120 already put in every payload.
