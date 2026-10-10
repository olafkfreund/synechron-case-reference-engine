---
status: draft
issue: 120
author: olafkfreund
---

# Intent: A document whose extract job failed is never retried on later crawls

## Problem

`ingest.ingest` (`app/ingest.py:79-87`) returns `skipped` when a document's
bytes are unchanged. It refreshes the ACL and the deleted flag, and does nothing
else. That assumes the first ingest finished, with an extract job that produced
a case.

The extract job can fail for good:

- The worker allows 3 attempts (`app/worker.py:71-73`).
- `CLAIM` picks the lowest queued id with no backoff, so a retry runs at once.
- A short Bedrock throttle or timeout can use up all 3 attempts in seconds, and
  the job ends `failed`.

From then on, every crawl sees the same checksum and skips the document. It
never gets a case, never reaches review, and doesn't show in search. Nothing in
the app requeues failed jobs, and the admin isn't told which documents are
stuck.

## Proposed outcome

- A crawl that sees an unchanged document with no case and no pending extract
  job, whose last extract job failed, queues the extract again.
- A transient model failure therefore heals on the next scheduled crawl,
  without admin action.
- A document that already has a case, or has an extract job queued or running,
  is skipped exactly as today.

## Affected users and systems

- `app/ingest.py` (the skip branch).
- The `jobs` table: new rows only, no schema change.
- Reviewers, indirectly: the cases appear for review.
- Tests: `tests/test_ingest.py`.
- Not affected: the worker's retry loop, the crawlers and the extraction
  itself.

## Constraints

- Never queue a second extract while one is queued or running for the same
  document.
- Reuse the failed job's payload, which holds the basis already decided. Don't
  re-run triage on a skip.
- No schema change.

## Open questions

1. **Should a failure the worker treats as permanent (`llm.PolicyError`, a
   model not approved) also be requeued on each crawl?**
   - **A. Requeue every failed job.** A policy failure is retried once per
     crawl, which is cheap and harmless (it fails fast, once a day) and heals
     once the model is approved.
   - **B. Requeue only non-policy failures**, telling them apart by the stored
     error text.

   **Recommendation: A.** It is simpler, and it heals after an approval too.
2. **Add backoff to the worker's immediate retries?** **Recommendation: no**,
   not in this issue. The crawl-time requeue is the backoff: it retries once a
   day.
