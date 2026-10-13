---
status: draft
issue: 218
intent: intent/2026-10-13-218-retriage-label.md
---

# Spec: A case retired by re-triage isn't labelled "Rejected in review"

## Design

- **`app/ingest.py:141`:**
  - When `basis` is None, the update sets `retired_by='retriage'`.
  - When the case is re-opened (`basis` set), it sets `retired_by=null`, as
    today.
  - So `retired_by = 'retriage' if not basis else None` goes in as a
    parameter.
- **`app/review.py:158-171` (`doc_status`):** for `case_status ==
  "rejected"`:
  - `'untick'` → `SKIP_CONTRACT`, unchanged
  - `'retriage'` → `("No longer delivery evidence (document changed)",
    "case")`
  - anything else → "Rejected in review", unchanged
- **Other users of `retired_by` are unchanged:**
  - `app/sources.py:49,52` compares only against `'untick'`
  - `app/extract.py:292` resets it to null

## Alternatives rejected

- **Inferring the reason from `approved_by is null` plus `kind`.** It's
  indirect and wrong for a case that was approved before its document
  changed.
- **A new column.** `retired_by` already exists for exactly this, the reason
  a case was retired.

## Risks

None found. Old rows keep the old label until their document changes again,
which the intent accepts.

## Verification

- **New test:** a delivered document is ingested, then changed so it triages
  as a proposal. Its case is `rejected`, `retired_by='retriage'`, and the
  Latest documents page shows the new label.
- **Unchanged label:** a reviewer rejection still shows "Rejected in review".
- **Full suite:**
  `docker compose build app && docker compose run --rm app pytest`.
