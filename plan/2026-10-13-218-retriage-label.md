---
status: approved
issue: 218
spec: spec/2026-10-13-218-retriage-label.md
---

# Plan: A case retired by re-triage isn't labelled "Rejected in review"

Worktree `../refs-engine-218`, branch `fix/218-retriage-label`. The change
is small, so the session implements it without a coder handoff.

## Approved decisions

- **`app/ingest.py:141`:** set `retired_by='retriage'` when the case is
  retired because `basis` is None, and null otherwise.
- **`app/review.py` `doc_status`:** `'retriage'` shows "No longer delivery
  evidence (document changed)" and links to the case. `'untick'` and the
  "Rejected in review" label for every other rejection are unchanged.

## Steps

1. **`app/ingest.py:141`:**

   ```python
   conn.execute("update cases set status=%s, retired_by=%s where document_id=%s",
                ("extracted" if basis else "rejected", None if basis else "retriage", doc_id))
   ```

   - Trap: keep the comment above it.
2. **`app/review.py:171`:** add a `RETRIAGED` label constant, and branch on
   `retired_by == "retriage"` before the fallback to "Rejected in review".
3. **Tests:**
   - **ingest:** a delivered document re-ingested as a proposal leaves the
     case rejected, with `retired_by='retriage'`.
   - **`doc_status` unit test:** the three rejected variants.
   - Then the full suite:
     `docker compose build app && docker compose run --rm app pytest`.

## Tests

As in step 3.

## Rollback

Revert the commit. Old rows with `'retriage'` then show "Rejected in review"
again, as they did before.

## Deviations

None yet.
