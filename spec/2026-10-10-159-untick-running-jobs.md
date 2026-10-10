---
status: draft
issue: 159
intent: intent/2026-10-10-159-untick-running-jobs.md
---

# Spec: after an untick, no flagged extract job writes a case

## Design

One guard in `extract()` (`app/extract.py`) covers every path a flagged job can
take: queued, running, failed and retried by `enqueue_crawls`, or started later.
"Flagged" means `basis_reason == "source marked executed"`. That is the reason
`QUEUE_CONTRACTS` (`app/sources.py`) and `ingest()` give a job queued only
because of the source flag.

- **Up front, so no model call is wasted.** The existing first select, `d.text,
  s.data_class`, also reads `s.config->'executed_contracts' = 'true'::jsonb`. If
  the job is flagged and the flag is not true, `extract()` returns without
  calling `build()`. The comparison is `= 'true'::jsonb`, the same "true, not
  truthy" rule `basis_for` applies (`config.get("executed_contracts") is True`).
- **At the write, under the admin's lock.** For a flagged job, just before the
  `insert into cases ... on conflict` upsert, `extract()` runs
  `select 1 from sources s join documents d on d.source_id = s.id where d.id = %s
  and s.config->'executed_contracts' = 'true'::jsonb for share of s`. If there is
  no row, it returns without writing. `update` in `app/sources.py` takes the
  source row `for update` and runs `RETIRE_FLAGGED` and `DROP_FLAGGED_JOBS` in
  that transaction, so both orders are correct:
  - **The untick commits first.** The `for share` waits for it, re-reads the
    row, sees the flag off and writes nothing.
  - **The extract writes first.** It holds `for share` until it commits. The
    admin's `for update` then waits, and `RETIRE_FLAGGED` rejects the case just
    written.
- **The job ends as `done`.** `extract()` returns normally, so the worker marks
  the job done. The daily retry in `enqueue_crawls` only requeues `failed`
  extracts, so the job never comes back.
- **Not flagged means unchanged.** `delivered` jobs and `executed contract`
  jobs skip both checks.

## Clashes

- **#132** adds a `checksum` argument and a `select 1 from documents where id=%s
  and checksum=%s for share` at the same point in `extract()`. Whichever lands
  second merges the two into one locking select that joins `documents` and
  `sources`: `... for share of d, s`, with the checksum and flag conditions in
  its `where`. Both early checks share the first select.
- **#160** edits `RETIRE_FLAGGED` and `QUEUE_CONTRACTS` in `app/sources.py`. This
  spec doesn't touch `app/sources.py`, so the two changes don't textually
  conflict.

## Alternatives rejected

- **Widen `DROP_FLAGGED_JOBS` to delete `failed` jobs too.** It still misses a
  `running` job, which only the write-time check can stop.
- **Have the untick mark running jobs cancelled.** The worker has no
  cancellation, and a check at the write is needed anyway.
- **Check only at the write.** That is correct but makes a needless model call.
  The constraints ask to skip the call when the outcome is known up front.

## Risks

- A flagged job runs its model call, and the untick lands during that call. The
  call's cost is spent, but no case is written. That is acceptable.
- An unflagged extract on a contract source holds no new lock, because the
  guard is only for flagged jobs.

## Verification

New tests in `tests/test_extract.py`, using the `doc` fixture with the source's
config set as each test needs. Each must fail on main:

- `test_flagged_job_on_unticked_source_writes_no_case_and_calls_no_model`:
  - The source has no `executed_contracts`.
  - `complete_json` raises if called.
  - `ex.extract(did, "engagement", "source marked executed")` returns, and no
    case row exists.
  - On main the model is called.
- `test_untick_during_flagged_extract_writes_no_case`:
  - The flag is true at the start.
  - The fake `complete_json` commits
    `update sources set config = config - 'executed_contracts'` in its own
    connection, then returns a valid case.
  - No case row exists afterwards. On main one is written.
- `test_executed_contract_reason_ignores_the_flag`:
  - The source has no flag, and the reason is `"executed contract"`.
  - A case is written.
  - This is a guard against over-blocking, and it passes on main by design.

Then the full suite: `docker compose build app && docker compose run --rm app
timeout 900 pytest -q -p no:cacheprovider`.
