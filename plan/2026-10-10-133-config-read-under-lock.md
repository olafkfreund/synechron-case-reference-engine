---
status: approved
issue: 133
spec: spec/2026-10-10-133-config-read-under-lock.md
---

# Plan: Toggling "contracts executed" during a crawl is lost for documents being converted

Approved design: `ingest()` reads only `data_class` before triage. The
`executed_contracts` config is read as the first statement of the write
transaction, with `select config from sources where id=%s for share`, and
`basis` and `reason` are computed there.

The admin save (`app/sources.py` `update`, line 113) already locks the source
row `for update` before it runs `QUEUE_CONTRACTS` or
`RETIRE_FLAGGED`/`DROP_FLAGGED_JOBS`. Whichever transaction commits first, the
toggle applies to the document:

- If the save commits first, ingest reads the new flag.
- If ingest commits first, the save's statements then see the new document row.

Out of scope: a `data_class` change during triage. That read stays early.

Coder handoff: no. Two steps in two files.

## Steps

1. **`tests/test_ingest.py`.** Add both tests after `test_contract_payload_and_source_flag`
   (line 105). Use the `env` fixture.
   - Both patch `ing.triage_text` with a function `(text, data_class)` that
     first changes the flag in its own `with db.connect() as c:` block, which
     commits on exit, and then returns
     `ing.Triage(kind="contract", describes_delivered_work=False, executed=False)`.
     This is the pattern `test_source_deleted_before_insert_raises` uses at
     line 453.
   - `test_executed_ticked_during_triage_queues_engagement`:
     - The fake runs `update sources set config=config || '{"executed_contracts": true}' where id=%s`.
     - After `ing.ingest(sid, "a", "a", b"x")`, the jobs for the source's
       documents are `[("engagement", "source marked executed")]`. Use the
       select from line 116.
   - `test_executed_unticked_during_triage_queues_nothing`:
     - Set the flag to true before ingest.
     - The fake runs `update sources set config=config - 'executed_contracts' where id=%s`.
     - After `ingest`, `jobs() == 0`.
   - Verify: `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_ingest.py -k "during_triage"`.
     Both tests fail on main code.
   - Traps:
     - There is no bind mount, so always build first.
     - Never run `docker compose up` or `down`.
     - Made-up names only.

2. **`app/ingest.py:88-99`, `ingest()`.**
   - Line 88: select only `data_class`. Line 91 becomes `data_class = src[0]`.
   - Move lines 98-99 (`basis = ...` and `reason = ...`) inside the second
     `with db.connect() as conn:` (line 100), before the documents upsert,
     preceded by:
     ```python
     # the flag is read under the lock an admin's save takes, so a toggle during triage applies here (#133)
     cfg = conn.execute("select config from sources where id=%s for share", (source_id,)).fetchone()
     if not cfg:
         raise LookupError(f"source {source_id} no longer exists")
     basis = basis_for(triage, cfg[0])
     ```
     `reason` keeps its expression unchanged.
   - Leave the upsert's own `for share` sub-select and its
     `if not got: raise LookupError` as they are.
   - Verify: step 1's command, then the full suite. These must still pass:
     - `test_contract_payload_and_source_flag`
     - `test_basis_for_routing_table`
     - `test_executed_flag_must_be_true_not_truthy`
     - `test_source_deleted_before_insert_raises`
     - the save tests in `tests/test_sources.py`
   - Traps:
     - Don't move the lock earlier than the write transaction. It must not be
       held across S3, Docling or triage.
     - The skip path (lines 83-87) doesn't use the config. Leave it alone.

## Tests

`docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`
must pass in full. Both new tests must fail against main's `app/ingest.py`.

## Rollback

Revert the two commits. There is no data or schema change.

## Deviations
- Review: the two planned tests commit the toggle before ingest's write transaction, so they passed without the lock. `test_ingest_waits_for_an_uncommitted_tick` holds an admin save open (row locked, flag set) while ingest runs; it fails if the `for share` is removed.
