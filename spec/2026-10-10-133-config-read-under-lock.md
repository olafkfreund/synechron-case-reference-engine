---
status: draft
issue: 133
intent: intent/2026-10-10-133-config-read-under-lock.md
---

# Spec: Toggling "contracts executed" during a crawl is lost for documents being converted

## Design

**`app/ingest.py` `ingest()`.** The early `select data_class, config from sources`
becomes `select data_class from sources`. `data_class` is still needed before
triage, because it decides which models may read the text.

The basis is now decided inside the write transaction. Its first statement locks
the source row and reads the config:

```python
with db.connect() as conn:
    # the flag is read under the lock an admin's save takes, so a toggle during triage applies here (#133)
    cfg = conn.execute("select config from sources where id=%s for share", (source_id,)).fetchone()
    if not cfg:
        raise LookupError(f"source {source_id} no longer exists")
    basis = basis_for(triage, cfg[0])
    reason = {...}.get(basis)   # unchanged expression, moved
    got = conn.execute("insert into documents ...")  # unchanged
```

The rest of the transaction is unchanged:
- the `insert ... on conflict` and its `for share` sub-select for `acl_groups`;
- the `update cases`;
- `reopen_merged`;
- the extract job insert.

The lock already held makes the sub-select's `for share` redundant. It stays,
because removing it would add diff and gain nothing.

**Why this closes both windows.** `sources.save` (`app/sources.py`, `save`)
locks the row `for update` before it changes the config. It then runs
`QUEUE_CONTRACTS`, or `RETIRE_FLAGGED` with `DROP_FLAGGED_JOBS`.

- **The save commits first.** `ingest` blocks on `for share`, reads the new
  config, and decides from it.
- **`ingest` takes the share lock first.** The save's `for update` waits for
  `ingest` to commit. The save then runs its statements, which now see the
  document row:
  - on tick, `QUEUE_CONTRACTS` queues an engagement job for it;
  - on untick, `DROP_FLAGGED_JOBS` deletes the job that `ingest` queued.

## Alternatives rejected

- **Re-reading the config after triage, without a lock.** A save can still
  commit between the read and the write, so the race shrinks but doesn't close.
- **Locking the source for the whole of `ingest()`.** That would hold a row
  lock across Docling and the model call, and block admin saves for minutes.
- **Having the save also re-triage documents that are mid-conversion.** The
  save can't see those documents, because no row exists yet.

## Risks

- **`data_class` toggled during triage** is not covered: triage has already
  used the old class. This is out of scope, and the intent asks to keep that
  read early. Worth its own issue if it matters.
- **Lock order.** `ingest`'s transaction already takes `for share` on the
  source. Taking it one statement earlier adds no new lock and no new order.
- **A source deleted during triage** still raises `LookupError`, now from the
  first statement.

## Verification

New tests in `tests/test_ingest.py`. Both must fail on main:

- `test_executed_ticked_during_triage_queues_engagement`:
  - the source has no flag;
  - the triage fake returns a non-executed `contract`, and first sets
    `config.executed_contracts = true` in its own committed connection;
  - after `ingest`, one extract job is queued with basis `engagement` and
    reason `source marked executed`.
- `test_executed_unticked_during_triage_queues_nothing`:
  - the source starts flagged;
  - the triage fake clears the flag;
  - after `ingest`, no job is queued for the document.

The existing tests must keep passing:
- `test_basis_for_routing_table`;
- `test_executed_flag_must_be_true_not_truthy`;
- the #64 save tests in `tests/test_sources.py`.

The full suite must pass.
