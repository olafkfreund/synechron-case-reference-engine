---
status: draft
issue: 64
intent: intent/2026-10-09-64-executed-flag-retriage.md
---

# Spec: Apply a source's "contracts executed" change to contracts already crawled

## Design

Everything happens in `update()` (`app/sources.py:74-96`), inside the
transaction it already opens (`:81`), right after the `update sources`
statement. Approved intent decisions: use the stored `documents.kind`
(no new triage); on, only contracts with no case; off, retire approved
engagements too.

**Detect a change.** The `select … for update` at `:82` also reads
`config->'executed_contracts' = 'true'` as `was`. The source row is
locked, so two admins saving at once can't both see the same old value.
The routes compare `was` with the posted `executed_contracts`; nothing
happens when they're equal.

**On (`not was and executed_contracts`).** Queue one `extract` job per
eligible contract, with the payload `ingest()` uses (`app/ingest.py:104-106`):

```sql
insert into jobs(kind, payload)
select 'extract', jsonb_build_object('document_id', d.id, 'basis', 'engagement',
                                     'basis_reason', 'source marked executed')
from documents d
where d.source_id = %s and d.kind = 'contract' and d.deleted_at is null
  and not exists (select 1 from cases c where c.document_id = d.id)
  and not exists (select 1 from jobs j where j.kind = 'extract' and j.status in ('queued', 'running')
                  and (j.payload->>'document_id')::bigint = d.id)
```

- "No case" leaves out contracts whose case was rejected, so a reviewer's
  rejection is never undone.
- The `jobs` check prevents duplicates when the flag is toggled on, off
  and on again before the worker runs, and when a crawl has just queued
  the same document.
- The worker's `extract` handler is unchanged. It reads `documents.text`
  and the source's data class, so model routing for confidential data is
  the same as for a crawl.

**Off (`was and not executed_contracts`).** Two statements:

1. Retire the engagements that exist only because of the flag, the same
   way `ingest()` retires a case (`status = 'rejected'`, `:101-102`):

   ```sql
   update cases c set status = 'rejected'
   from documents d
   where d.id = c.document_id and d.source_id = %s
     and c.basis = 'engagement' and c.data->>'basis_reason' = 'source marked executed'
     and c.status <> 'rejected'
   ```

   This covers approved ones too. Search only returns `approved` cases
   (`app/search.py:58`), so they leave search at once. `executed
   contract` engagements and delivered cases aren't touched.
2. Cancel the queued jobs that would recreate them:

   ```sql
   delete from jobs j using documents d
   where j.kind = 'extract' and j.status = 'queued'
     and (j.payload->>'document_id')::bigint = d.id and d.source_id = %s
     and j.payload->>'basis_reason' = 'source marked executed'
   ```

   Without this, a job queued by the "on" save (or by a crawl while the
   flag was on) would still create an engagement after the flag went off.

**Help text** (`app/templates/sources.html:5`): replace "A change applies
to documents crawled or changed afterwards; contracts already crawled
keep their current result." with "Ticking it also queues contracts
already crawled that have no case; unticking it retires the engagements it
created, approved ones included."

**`create()`** is unchanged: a new source has no documents.

## Alternatives rejected

- **Re-running triage per contract** (a new job type, an LLM call per
  document). With the flag on, the answer doesn't change the basis
  (intent question 1).
- **Re-opening rejected contract cases.** A reviewer's rejection can't be
  told apart from a retirement (intent question 2).
- **Retiring only open engagements.** Approved ones would stay in search
  although the admin withdrew the assumption (intent question 3).
- **A worker job that does the re-queue or retirement later.** It would
  split the flag change from its effect across transactions; the SQL is
  small and indexed by `source_id`.
- **Checking the flag in the worker before extracting.** It would also
  fix the off race, but it changes the extract path every crawl uses; the
  queued-job delete is local to this change.

## Risks

- **A job already `running` when the flag goes off** still finishes, and
  its case is created as `extracted` with "source marked executed". It's
  a narrow window (one extraction). The next "off" save, or the reviewer,
  handles it. Recorded, not fixed.
- **A large source.** "On" queues one job per contract with no case. The
  worker takes them one at a time, as after a first crawl.
- **Cases retired by mistake** (the admin unticks the box): ticking it
  again doesn't bring them back, because they now have a case
  (`rejected`). The help text says unticking retires them. To restore
  them, re-crawl after changing the document, or approve them through
  review once they're re-opened. This is the price of never undoing a
  reviewer's rejection.

## Verification

Tests in `tests/test_sources.py` (admin client from `tests/test_auth.py`;
documents and cases inserted with SQL, no S3 or LLM):

- **On:** a source with three contracts (one with no case, one with a
  `rejected` case, one deleted) and one `case` document. Tick the flag →
  exactly one `extract` job, for the contract with no case, with the
  basis `engagement` and the reason `source marked executed`. Save again
  with the flag on → still one job.
- **Off:** a source with the flag on, with cases: an approved engagement
  with "source marked executed", an approved engagement with "executed
  contract", and a delivered case; plus a queued "source marked executed"
  job. Untick → only the first is `rejected`, and the queued job is gone.
- **No change:** saving without changing the flag queues nothing and
  retires nothing.

Then the full suite (`docker compose build app && docker compose run --rm
app pytest`), and a manual run in the local portal (#71) with a made-up
contract.
