---
status: approved
issue: 160
author: olafkfreund
---

# Intent: re-ticking "contracts executed" brings the contracts back

## Problem

Ticking "contracts executed" on a source queues an engagement extract for
each of its crawled contracts (`QUEUE_CONTRACTS` in `app/sources.py`).
Unticking it rejects the cases those jobs made (`RETIRE_FLAGGED`), but
leaves the case rows in place.

`QUEUE_CONTRACTS` skips any document that already has a case. So after
tick, untick, tick again, nothing is queued. The crawl doesn't help: the
same-bytes skip in `ingest()` never triages the documents again. The
contracts stay rejected until their bytes change, and the admin's second
tick silently does nothing.

## Proposed outcome

- Ticking "contracts executed" again queues every contract whose case the
  untick had rejected, and they come back as engagement cases for review.
- A case a reviewer rejected stays rejected, whatever the source's flag
  does.
- Contracts never extracted are queued as today.

## Affected users and systems

- Admins who toggle "contracts executed".
- Reviewers, who see these cases again in the queue.
- `app/sources.py` (`QUEUE_CONTRACTS`, `RETIRE_FLAGGED`), and possibly
  `sql/schema.sql`.

## Constraints

- A reviewer's decision must never be undone by an admin's setting.
- The re-queued job still carries the document's checksum (#120).
- #159 changes the untick path in the same file. Whichever lands second
  builds on the other.

## Open questions

1. **How is the untick's reject told apart from a reviewer's?** Today both
   leave `status='rejected'` and nothing else, with the same `basis` and
   `basis_reason`.
   - **A. Don't tell them apart.** Re-ticking re-queues every rejected
     case with reason "source marked executed". A reviewer's reject of
     one of these comes back for review, and they reject it again.
   - **B. The untick marks its rejects.** A new nullable column
     `cases.retired_by` is set to `'untick'` by `RETIRE_FLAGGED`, and
     cleared whenever the case is written again (`extract`, `ingest`, the
     review actions). `QUEUE_CONTRACTS` also queues documents whose case
     is `rejected` with `retired_by = 'untick'`. Cases unticked before
     this change carry no mark and stay rejected. An admin can bring them
     back by changing the file, or a one-off update can mark them.
   - **C. Reviewers' rejects are marked instead.** `reject` records
     `rejected_by`, and a rejected case without it counts as the
     untick's. That would wrongly count ingest's "no longer delivered
     work" rejects, and rejects made before the change, as the untick's.

   **Recommendation: B.** It is the only option that keeps a reviewer's
   decision and is exact. It is one column, set in one statement and read
   in one statement, and the unmarked old rows fail safe by staying
   rejected.

**Decision (approved by olafkfreund, 2026-10-10): B.**
