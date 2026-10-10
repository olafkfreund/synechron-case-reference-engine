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
