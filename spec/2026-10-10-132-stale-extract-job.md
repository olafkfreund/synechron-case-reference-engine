---
status: approved
issue: 132
intent: intent/2026-10-10-132-stale-extract-job.md
---

# Spec: A queued extract job revives a document that a newer version retired

## Design

The approved decision is **B**: `extract()` checks the job's checksum against
the document's current one, and does nothing when they differ.

**`app/worker.py` `HANDLERS["extract"]`** passes the payload's checksum:

```python
"extract": lambda p: extract.extract(p["document_id"], p.get("basis", "delivered"),
                                     p.get("basis_reason", ""), p.get("checksum")),
```

**`app/extract.py` `extract()`** takes `checksum: str | None = None` and checks
it twice. A job with no checksum, queued before #120, skips both checks and runs
as today.

1. **Early check.** The first select also reads `d.checksum`. If the checksum
   was given and differs, it returns before `build()`, so no model call is
   wasted.
2. **Write-time check.** `build()` calls the model, which can take minutes, and
   a newer version can land during the call. So, right before the upsert, in the
   same transaction:

   ```python
   # the version this job was queued for must still be current (#132)
   if checksum and not conn.execute("select 1 from documents where id=%s and checksum=%s for share",
                                    (document_id, checksum)).fetchone():
       return
   ```

   `for share` waits for any open `ingest()` transaction that is updating the
   row. Under read committed, it then re-reads the row:
   - **If ingest committed a new checksum,** no row comes back and the job
     writes nothing.
   - **If our upsert commits first,** ingest's later `update cases set
     status='rejected'` retires the case, as it does today.

   Either way, no `extracted` case survives for a retired version.

A stale job returns normally, so the worker marks it `done` and doesn't retry
it. Nothing is logged, matching how the other skips in `ingest()` behave.

## Alternatives rejected

- **A. `ingest()` deletes the document's queued extract jobs.** It misses a job
  that is already running, which is the slow-model window above. Rejected at
  intent approval.
- **The write-time check alone.** It is correct, but each stale job still
  spends one model call. The early check costs one extra column on an existing
  select.
- **A `where exists` on the upsert itself.** The `on conflict ... do update`
  path makes that awkward. A separate `for share` select is simpler and gives
  the same locking.

## Risks

- **Pre-#120 jobs** have no checksum and keep today's race. They drain after
  one queue pass, and #120's daily requeue copies the payload, which carries a
  checksum after #120.
- **`scripts/eval_extraction.py`** calls `build()`, not `extract()`, so it is
  unaffected.
- **Merged cases.** Extract writes one document's case. Merging works on cases
  after extraction, so the check doesn't change merge behaviour.
- **Lock wait.** `for share` waits at most for one ingest transaction, which
  holds no model call.

## Verification

New tests that must fail on main:

- `test_stale_checksum_writes_no_case`: the document has checksum `x`.
  `extract(did, checksum="old")` leaves no case row, and the model fake is never
  called.
- `test_version_replaced_during_model_call_writes_no_case`: the model fake
  updates `documents.checksum` to `y` in its own connection, then returns a
  good reply. `extract(did, checksum="x")` leaves no case row.
- `test_extract_handler_passes_checksum` in `tests/test_worker.py`: with
  `extract.extract` monkeypatched, the handler hands `p["checksum"]` through.

This test must pass on main and after the fix:

- `test_no_checksum_still_extracts` in `tests/test_extract.py`:
  `extract(did)` with no checksum still writes an `extracted` case, so old
  jobs are unaffected.

The full suite must pass.
