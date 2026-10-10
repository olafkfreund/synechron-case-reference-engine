---
status: draft
issue: 120
intent: intent/2026-10-10-120-failed-extract-retry.md
---

# Spec: A document whose extract job failed is never retried on later crawls

## Design

The approved answers are:

- requeue the last failed extract job, policy failures included;
- no worker backoff.

**The approved outcome holds, but the mechanism differs from the intent's
wording.** The intent says "a crawl that sees an unchanged document" requeues
the extract. Reading the crawlers shows a crawl usually never sees one:

- **S3** skips a known key older than the cursor before downloading it
  (`app/crawl.py:58`), so `ingest()` is never called.
- **SharePoint** uses delta, which only offers changed items.
- **Confluence** uses CQL `lastmodified >= cursor` and `skip_unchanged`
  (`:418`), not `ingest()`.

A requeue in `ingest()`'s skip branch would only fire inside S3's one-day
slack. So the requeue goes in the daily scheduler instead,
`app/enqueue_crawls.py`, which already runs once a day for every source. That
covers every crawler and the "heals on the next scheduled crawl" outcome.

`main()` gains one statement after the crawl loop:

```python
# an extract that failed all its attempts is requeued once a day until it yields a case (#120)
requeued = conn.execute("""
    insert into jobs(kind, payload)
    select 'extract', j.payload from (
        select distinct on ((payload->>'document_id')::bigint) payload, status
        from jobs where kind = 'extract' order by (payload->>'document_id')::bigint, id desc) j
    join documents d on d.id = (j.payload->>'document_id')::bigint and d.deleted_at is null
    where j.status = 'failed' and not exists (select 1 from cases c where c.document_id = d.id)""").rowcount
```

- **Only the latest job per document counts.** When that job is `queued` or
  `running`, nothing is added, so there is never a second pending extract.
  When it is `done`, nothing is added either.
- **The payload is the failed job's own**, so its basis and basis reason are
  reused. Triage does not run again.
- **No case, not deleted.** A document that has a case is skipped, whatever
  the case's status (rejected or merged included). So is a document that was
  withdrawn.
- **Logging.** The print line reports both counts:
  `queued N crawl job(s), M extract retry(ies)`.
- **Return value.** `main()` still returns the crawl count. Its existing
  callers and tests use that count.

## Alternatives rejected

- **Requeue in `ingest()`'s skip branch (the intent's wording).** As above, it
  doesn't fire for most stuck documents.
- **Requeue in each crawler.** That is three copies of the same rule, and each
  crawler would need a new pass over its known documents.
- **Backoff in the worker.** Answer 2 rejected it. The daily requeue is the
  backoff.
- **Requeue only failures that are not policy failures.** Answer 1 rejected
  it.

## Risks

- **A document that always fails is retried once a day, forever.** That is
  one cheap failure per day each, and it is visible in the jobs table. A
  policy failure fails fast, before any model call. This was accepted in
  answer 1.
- **Query cost.** One `distinct on` over the extract jobs, once a day. It is
  fine at the expected thousands of jobs. If it ever matters, the upgrade is
  an index on `(kind, (payload->>'document_id'))`.
- **No schema change, no infrastructure change.** The scheduler task
  (`infra/ecs.tf`) already runs this module.

## Verification

Tests go in the test file that covers `enqueue_crawls`, or in
`tests/test_ingest.py` next to `JOBS_OF`:

- An ingested document whose extract job is marked `failed` and that has no
  case gets exactly one new `queued` extract with the same payload when
  `main()` runs. A second `main()` adds none, because the latest job is now
  `queued`. The test fails on main.
- No requeue happens when:
  - the document has a case;
  - the document is deleted;
  - the latest job is `done`.
- The full suite passes.
