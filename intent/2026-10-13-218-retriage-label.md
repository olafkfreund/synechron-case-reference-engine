---
status: draft
issue: 218
author: olafkfreund
---

# Intent: A case retired by re-triage isn't labelled "Rejected in review"

## Problem

Sometimes a changed document no longer triages as delivered work. When that
happens, `app/ingest.py:141` sets its case to `status='rejected'` with
`retired_by=null`. That is the same state a reviewer's rejection leaves, so
the "Latest documents" page (`app/review.py:171`) shows "Rejected in review".
That's wrong: nobody rejected the case. The document changed and stopped
qualifying.

## Proposed outcome

- **Ingest records the reason.** When ingest retires a case this way, it
  sets `retired_by='retriage'`, next to the existing `'untick'` (#160).
- **New label.** `doc_status` shows "No longer delivery evidence (document
  changed)" for it, linked to the case.
- **Reviewer rejections** still show "Rejected in review".
- **Unchanged:**
  - a re-extraction resets `retired_by` to null (`app/extract.py:292`)
  - the source-flag logic, `retired_by is distinct from 'untick'` at
    `app/sources.py:49`, treats `'retriage'` like any other rejection, as
    it treats null today
- **No backfill.** Old rows keep their label until their document changes
  again.

## Affected users and systems

- **Users:** reviewers, on the "Latest documents" page.
- **Files:** `app/ingest.py`, `app/review.py` and a test. There is no schema
  change, because `retired_by` is free text.

## Constraints

None beyond keeping the `'untick'` behaviour unchanged.

## Open questions

None.
