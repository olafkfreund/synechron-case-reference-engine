---
status: draft
issue: 160
intent: intent/2026-10-10-160-retick-contracts.md
---

# Spec: re-ticking "contracts executed" brings back what the untick retired

## Design

Decision B: the untick marks the cases it rejects, and re-ticking re-queues only
those.

- **Schema, `sql/schema.sql`.** Add, next to the other `alter table cases`
  lines:
  `alter table cases add column if not exists retired_by text;  -- 'untick': rejected by the source flag (#160)`.
  The repo applies schema changes this way: `schema.sql` is idempotent, and
  `db.init()` runs it. In AWS that happens from `app.migrate` as the schema
  owner. Locally it runs on start (`DB_INIT_ON_START=1`) and in tests through
  `db.init()`. The app role's grants are on whole tables (`sql/roles.sql`), so
  a new column needs no grant.
- **`RETIRE_FLAGGED` (`app/sources.py`)** sets `status='rejected',
  retired_by='untick'`. Nothing else changes: the same `where`, the same
  `returning`.
- **`QUEUE_CONTRACTS`.** The "no case yet" condition becomes "no case, or a case
  the untick retired":
  `and not exists (select 1 from cases c where c.document_id=d.id and c.retired_by is distinct from 'untick')`.
  The payload keeps `d.checksum` (#120). The "no queued or running job" guard
  stays.
- **The mark is cleared whenever the case is written again,** so a later
  reviewer reject is never mistaken for the untick's:
  - the `extract()` upsert (`app/extract.py`) adds `retired_by=null` to its
    `do update set` list;
  - `ingest()`'s `update cases set status=%s where document_id=%s`
    (`app/ingest.py`) adds `retired_by=null`.

  The review actions don't need it. A rejected case is not `OPEN`, so it can't
  be edited, approved or rejected. `unmerge` keeps a rejected member rejected
  and leaves its mark as it is, which is correct.
- **Old rows.** Cases unticked before this change have `retired_by` null and
  stay rejected, as the intent decided. A one-off
  `update cases set retired_by='untick' ...` is possible but not part of this
  change.

## Clashes

- **#159** guards the flagged write in `extract()`. This spec adds one column to
  the same upsert's `set` list. They touch adjacent lines with no conflict in
  meaning.
- **#159** does not touch `app/sources.py`. **#132** adds a checksum check
  before the same upsert. Both rebase textually.

## Alternatives rejected

- **A, don't distinguish.** A reviewer's reject would come back on every
  re-tick, which the constraints forbid.
- **C, mark reviewers' rejects instead.** Ingest's "no longer delivered work"
  rejects and all old rejects would then count as the untick's.
- **A `rejected_reason` enum.** It is more general, but only one value is
  needed.

## Risks

- **A contract ingested while the source was unticked** is rejected by
  `ingest()` with no mark, so re-ticking does not bring it back. On main it
  didn't either. The case row blocks `QUEUE_CONTRACTS` today. This is out of
  scope, noted for a follow-up.
- **Merged members** keep their mark through `unmerge`, and re-ticking
  re-queues them as single cases. That matches what the untick undid.

## Verification

New tests in `tests/test_sources.py`, reusing `_src`, `_doc`, `_case` and
`REASON`:

- `test_reticking_brings_back_untick_rejects`:
  - Start from a flagged source with an approved flagged case.
  - Post to untick it. The case is now `rejected`, with `retired_by='untick'`.
  - Post to tick it again. One queued `extract` job exists for the document,
    with its checksum.
  - On main no job is queued, so the test fails there.
- `test_reticking_keeps_a_reviewers_reject`:
  - A flagged case rejected with no mark stays rejected after a tick, and no
    job is queued.
  - This is a guard, and it passes on main by design.
- `test_extract_clears_retired_by` (`tests/test_extract.py`):
  - A case with `retired_by='untick'` is re-extracted, and its `retired_by` is
    null.
  - On main the column does not exist, so the test fails there.

Then the full suite: `docker compose build app && docker compose run --rm app
timeout 900 pytest -q -p no:cacheprovider`.
