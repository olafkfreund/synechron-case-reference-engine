---
status: draft
issue: 159
spec: spec/2026-10-10-159-untick-running-jobs.md
---

# Plan: after an untick, no flagged extract job writes a case

Approved decisions, carried over from the spec:

- One guard in `extract()` (`app/extract.py`) covers every path a flagged job can
  take: queued, running, failed and retried by `enqueue_crawls`, or started later.
- "Flagged" means `basis_reason == "source marked executed"`, the reason
  `QUEUE_CONTRACTS` (`app/sources.py`) and `ingest()` give a job queued only
  because of the source flag. `delivered` and `executed contract` jobs skip both
  checks and are unchanged.
- **Up front:** the first select also reads
  `s.config->'executed_contracts' = 'true'::jsonb`. A flagged job whose flag is
  not true returns before `build()`, so no model call is made. The rule is "true,
  not truthy", as in `basis_for`.
- **At the write:** for a flagged job, just before the cases upsert, a
  `select ... for share` on the source row with the flag condition. No row means
  return without writing. `update` in `app/sources.py` takes the source row
  `for update` and runs `RETIRE_FLAGGED` and `DROP_FLAGGED_JOBS` in the same
  transaction, so: untick first → the share lock waits, re-reads, sees the flag
  off, writes nothing; extract first → the admin waits, and `RETIRE_FLAGGED`
  rejects the case just written.
- The job returns normally, so the worker marks it `done`; the daily retry only
  requeues `failed` extracts.
- `app/sources.py` is not touched (#160 edits it).

One refinement of the spec's #132 merge note: the spec suggests one combined
`... for share of d, s`. Postgres locks the rows of one statement in plan order,
which can take the document before the source. The admin's save locks the source
first, then documents (the order #132 established), so a combined lock could
deadlock against it. The plan keeps two statements instead: the flagged source
lock first, then #132's existing document checksum lock. Same guarantees, fixed
order.

The code below is `extract()` as it is after #132 (PR #175, branch
`fix/132-stale-extract-job` in `refs-engine-132`, `app/extract.py` lines
141–159).

## Steps

0. Rebase on main after #175 (#132) has merged: `git fetch origin && git rebase
   origin/main`. → verify by `grep -n "checksum=%s for share" app/extract.py`.
   Traps: build on #132's `extract(document_id, basis, basis_reason, checksum)`;
   do not hand-copy its lines.

1. `app/extract.py`, `extract()`: both checks.
   - Before the `with` (top of the function): `flagged = basis_reason == "source
     marked executed"  # queued only because of the source flag (#159)`.
   - The first select (line 143) adds a fourth column:
     `s.config->'executed_contracts' = 'true'::jsonb`.
   - After the checksum early return (line 147), add:
     ```python
     if flagged and row[3] is not True:
         return  # the source was unticked: no case, and no model call (#159)
     ```
   - After `data = case.model_dump()` and before #132's checksum share lock
     (line 151), add:
     ```python
     # under the lock the admin's save takes, so an untick during the model call applies here (#159)
     if flagged and not conn.execute(
             "select 1 from sources s join documents d on d.source_id = s.id where d.id = %s "
             "and s.config->'executed_contracts' = 'true'::jsonb for share of s", (document_id,)).fetchone():
         return
     ```
   → verify by the tests in step 2.
   Traps:
   - `row[3]` is `None` when the key is missing; `is not True` treats that as
     off. Do not use `not row[3]` logic that would let a JSON string `"true"`
     through: the SQL `= 'true'::jsonb` already makes a string false.
   - Lock order: source before document. Keep this check above the checksum
     lock, never merged into it.
   - Returning (not raising) is what makes the job `done`.

2. `tests/test_extract.py`: three tests after `test_no_checksum_still_extracts`
   (line 272), using the `doc` fixture and `cases_for`. The fixture's source has
   `config '{}'` (schema default); set it per test with
   `update sources set config = config || '{"executed_contracts": true}'
   where id = (select source_id from documents where id=%s)`.
   - `test_flagged_job_on_unticked_source_writes_no_case_and_calls_no_model(doc,
     monkeypatch)`: no flag; patch `ex.complete_json` to raise
     `AssertionError("model called")`;
     `ex.extract(did, "engagement", "source marked executed")`;
     `assert cases_for(did) == []`.
   - `test_untick_during_flagged_extract_writes_no_case(doc, monkeypatch)`: flag
     on; the fake `complete_json` runs `update sources set config = config -
     'executed_contracts' where id = (...)` in `with
     db.connect(autocommit=True)`, then returns `case()`; run as above;
     `assert cases_for(did) == []`.
   - `test_executed_contract_reason_ignores_the_flag(doc)`: no flag; reply
     `case()`; `ex.extract(did, "engagement", "executed contract")`;
     `assert cases_for(did) == [("extracted",)]`.
   - `test_flagged_extract_waits_for_an_uncommitted_untick(doc)` (beyond the
     spec's list: the test above passes with a plain select too, so this one
     proves the share lock). Copy `test_extract_waits_for_an_uncommitted_new_version`
     (line 253): flag on, reply `case()`; a `holder = db.connect()` runs the
     untick update without committing; start `ex.extract(did, "engagement",
     "source marked executed")` in a thread; `_wait_for_lock_wait(holder)`;
     commit; join; `assert not t.is_alive() and cases_for(did) == []`.
     Without `for share of s` the extract reads the old (flag on) row and writes
     a case, so this fails if the lock is removed.
   → verify by `docker compose build -q app && docker compose run --rm app pytest
   -q -p no:cacheprovider tests/test_extract.py`.
   Traps:
   - `_wait_for_lock_wait` blocks until the extract waits on the lock; if the
     extract instead returns early it never waits, so give the up-front read the
     flag on (it is: the holder has not committed).
   - The first two must fail on main (model called; case written); the third
     passes on main by design. Check with the scratchpad `check.sh` after
     committing.

## Tests

- The two new guard tests fail on main and pass on the branch; the third passes on both.
- Full suite: `docker compose build -q app && docker compose run --rm app timeout
  900 pytest -q -p no:cacheprovider`, all green.

## Rollback

Revert the branch's commits. No schema or data change.
