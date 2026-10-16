---
status: approved
issue: 120
spec: spec/2026-10-10-120-failed-extract-retry.md
---

# Plan: A document whose extract job failed is never retried on later crawls

These decisions are copied from the spec:

- **Where:** the daily scheduler `app/enqueue_crawls.py` requeues failed
  extracts, not `ingest()`'s skip branch. Crawls rarely see an unchanged
  document: S3 skips old keys before download, SharePoint delta only offers
  changes, and Confluence filters by cursor.
- **Which jobs:** for each document, only its latest extract job counts. It is
  requeued, using that job's own payload, when all of these hold:
  - its status is `failed`;
  - the document is live (`deleted_at is null`);
  - the document has no case at all.
- **Policy failures are requeued too.** There is no worker backoff.
- **`main()` still returns the crawl count.** The print line also reports the
  requeue count.

## Steps

1. `app/enqueue_crawls.py`: inside the same `with db.connect()` block, after the
   crawl loop, add the statement from the spec:
   ```python
   # an extract that failed all its attempts is requeued once a day until it yields a case (#120)
   retried = conn.execute(
       "insert into jobs(kind, payload) select 'extract', j.payload from ("
       "select distinct on ((payload->>'document_id')::bigint) payload, status from jobs where kind = 'extract' "
       "order by (payload->>'document_id')::bigint, id desc) j "
       "join documents d on d.id = (j.payload->>'document_id')::bigint and d.deleted_at is null "
       "where j.status = 'failed' and not exists (select 1 from cases c where c.document_id = d.id)").rowcount
   ```
   Change the print to `print(f"queued {queued} crawl job(s), {retried} extract retry(ies)", flush=True)`.

   Verify with `pytest -q tests/test_enqueue.py`: the existing tests pass.

   Traps:
   - The SQL has no `%s` parameters, so do not double the `%`. psycopg only
     treats `%` specially when params are passed. Keep it that way: pass no
     params.
   - A merged case has `document_id` null and its members keep their own case
     rows, so the `not exists` already treats members as having a case.

2. `tests/test_enqueue.py`: add `test_failed_extract_is_requeued_once`. Reuse
   `tests/test_ingest.py`'s `env` fixture by import, as other files do
   (`from tests.test_ingest import env, SID  # noqa: F401`).
   1. `ing.ingest(sid, "in/a.docx", "a.docx", b"one")`.
   2. Set that document's extract job to `status='failed'`.
   3. `enqueue_crawls.main()`. Now there is exactly one `queued` extract for
      the document, with a payload equal to the failed one.
   4. `main()` again. Still one queued.

   Add a second test, `test_no_requeue_with_case_deleted_or_done`, covering
   three variants:
   - a case row exists for the document;
   - `deleted_at` is set;
   - the latest job is `done`.

   Each adds no job.

   Verify that the first test fails on main.

   Traps:
   - `env` runs under `mock_aws()`, and ingest writes to S3 there. That is fine.
   - `main()` also queues crawl jobs for every enabled source, including the
     `env` source. Count only `kind='extract'` jobs for the test's document.
   - The `srcs` fixture in this file deletes `crawl%` jobs. `env` cleans up
     the extract jobs for its own source.
   - To insert a case for the variant, copy the minimal insert used in
     `tests/test_ingest.py`'s `merged_pair` (`:304`).

## Tests

The full suite must pass, and the requeue test must fail on main.

## Rollback

Revert the commit. Any extra queued extract jobs are harmless. Each either
succeeds or fails again.

## Deviations

- **Step 2:** the tests call `main()` through a small helper,
  `_run_main_only_for(sid)`. `main()` queues crawl jobs for every enabled
  source and requeues other tests' failed extracts in the shared test DB. The
  helper deletes every job `main()` added except this source's extract
  retries, so no stray queued job leaks into later tests, such as the worker's
  claim order. The "case" variant inserts a bare `cases(document_id, status)`
  row, which is all the `not exists` needs.
- **Review fix (blocking):** a failed v1 extract must not run on v2.
  - **The problem:** if v2 of a document is no longer a case, `ingest()`
    queues nothing. So v1's failed job stayed the latest one, and the retry
    would have extracted v2's text with v1's basis.
  - **The fix:** both extract-job builders put the document's `checksum` in
    the payload. Those are `app/ingest.py`, and `QUEUE_CONTRACTS` in
    `app/sources.py`. The retry now requires
    `d.checksum = j.payload->>'checksum'`.
  - **Older jobs:** jobs queued before this change have no checksum, so they
    are never retried. That is the safe direction.
  - **Files:** this adds two app files to the plan.
- **Review fix (should-fix):** documents of disabled sources are not retried
  (`join sources s ... and s.enabled`).
- **Review fix (tests):** new tests cover:
  - the latest job wins, both orders (older failed then newer done, and the
    reverse);
  - a disabled source;
  - a newer version that is no longer a case.
- **`tests/test_hardening.py`:** the `print` allow-list entry for
  `enqueue_crawls.py` now holds the new line. It is still counts only, with
  no content.
