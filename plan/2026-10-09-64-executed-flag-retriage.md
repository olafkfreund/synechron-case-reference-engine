---
status: draft
issue: 64
spec: spec/2026-10-09-64-executed-flag-retriage.md
---

# Plan: Apply a source's "contracts executed" change to contracts already crawled

## Approved decisions (self-contained)

- **Where:** everything goes in `update()` (`app/sources.py:74-96`), in
  its existing transaction, after the `update sources` statement.
  `create()` is unchanged.
- **Change detection:** the locked select at `:82` also reads `was`
  (`config->'executed_contracts' = 'true'`; a missing key counts as
  false). Nothing new happens when `was == executed_contracts`.
- **On** (`not was and executed_contracts`): one `extract` job per
  document of the source with:
  - `kind = 'contract'` and `deleted_at is null`;
  - no row in `cases` at all, so a rejected case is never re-opened;
  - no `extract` job for it already `queued` or `running`.

  The payload is `{document_id, basis: 'engagement', basis_reason: 'source
  marked executed'}`, as in `ingest()` (`app/ingest.py:104-106`). The
  stored `documents.kind` is used: no new triage, no LLM call in the
  request.
- **Off** (`was and not executed_contracts`):
  - set `status = 'rejected'` on the source's cases with `basis =
    'engagement'`, `data->>'basis_reason' = 'source marked executed'` and
    `status <> 'rejected'`. Approved ones are included. `executed
    contract` engagements and delivered cases aren't touched;
  - delete the source's `queued` `extract` jobs whose `payload->>'basis_reason'
    = 'source marked executed'`.
- **Help text** (`app/templates/sources.html:5`): the limitation
  sentence becomes "Ticking it also queues contracts already crawled that
  have no case; unticking it retires the engagements it created, approved
  ones included."
- **Accepted risk:** an extraction already `running` when the flag goes off
  still creates its case. It isn't fixed here.
- **Coder handoff:** 3 file-editing steps in 3 files. Steps 1–3 go to the
  `coder` agent. The session model does the manual run and the review.

## Steps

1. **Route.** `app/sources.py` `update()`:
   - line 82: `select data_class, acl_groups, coalesce(config->'executed_contracts'
     = 'true', false) from sources where id=%s for update`, then `was =
     old[2]`;
   - after the `update sources` statement (`:85-87`) add:

     ```python
     if executed_contracts and not was:  # contracts already crawled become engagements too (#64)
         conn.execute(QUEUE_CONTRACTS, (sid,))
     elif was and not executed_contracts:  # and stop being engagements, approved ones included
         conn.execute(RETIRE_FLAGGED, (sid,))
         conn.execute(DROP_FLAGGED_JOBS, (sid,))
     ```

   - the three SQL strings are module constants near the top, with the SQL
     from the spec:
     - `QUEUE_CONTRACTS`: `insert into jobs … select … from documents d
       where d.source_id=%s and d.kind='contract' and d.deleted_at is null
       and not exists (cases) and not exists (extract jobs queued/running
       for d.id)`;
     - `RETIRE_FLAGGED`: `update cases c set status='rejected' from
       documents d where d.id=c.document_id and d.source_id=%s and
       c.basis='engagement' and c.data->>'basis_reason'='source marked
       executed' and c.status<>'rejected'`;
     - `DROP_FLAGGED_JOBS`: `delete from jobs j using documents d where
       j.kind='extract' and j.status='queued' and
       (j.payload->>'document_id')::bigint=d.id and d.source_id=%s and
       j.payload->>'basis_reason'='source marked executed'`.

   → verify by `docker compose build app && docker compose run --rm app
   pytest tests/test_sources.py tests/test_ingest.py`: the existing tests
   pass.

   Traps:
   - `executed_contracts` is a `bool` form value; `was` must be a Python
     bool, not the jsonb text;
   - keep everything inside the `with db.connect() as conn:` block, so it
     commits with the flag;
   - the existing ACL copy and logging lines stay as they are;
   - there is no bind mount in compose: build before running tests.
2. **Help text.** `app/templates/sources.html:5`: replace the sentence
   "A change applies to documents crawled or changed afterwards; contracts
   already crawled keep their current result." with the one above.

   → verify by `grep -n "retires the engagements" app/templates/sources.html`.
   Traps: none.
3. **Tests.** `tests/test_sources.py`, using the `env`, `client` and `ADMIN`
   fixtures it already imports from `tests/test_auth.py`. Insert sources,
   documents, cases and jobs with SQL, with no S3 or LLM, and clean up in
   `finally`, as `test_sources_admin` does (delete jobs by `document_id`
   of the source's documents, then the source; documents and cases
   cascade, or delete them explicitly if they don't):
   - a helper `src(executed)` creates an `upload` source with config
     `{"bucket": "b"}` (plus `executed_contracts` when true) and
     `acl_groups` `{g1}`, and returns its id. A helper `doc(sid, kind,
     deleted=False)` returns a document id; `case(did, status, basis,
     reason)` inserts a case whose `data` is a minimal `ReferenceCase`
     JSON with `basis` and `basis_reason` set;
   - `test_ticking_executed_queues_contracts_without_a_case`: contracts
     A (no case), B (`rejected` case) and C (deleted), and a `case`-kind
     document D. Post the update with `executed_contracts=on` and
     `acl_groups=g1` → exactly one `extract` job, for A, with the payload
     basis `engagement` and the reason `source marked executed`. Post
     again with the flag on → still one job;
   - `test_unticking_executed_retires_flagged_engagements`: a source
     with the flag on and cases: E1 (approved, engagement, "source marked
     executed"), E2 (approved, engagement, "executed contract"), E3
     (approved, delivered); plus a queued extract job for a contract with
     reason "source marked executed". Post with the flag off → E1
     `rejected`, E2 and E3 still `approved`, and the queued job is gone;
   - `test_saving_without_flag_change_does_nothing`: a source with the
     flag off and contract A with no case; post with the flag off → no
     jobs. A source with the flag on and E1 approved; post with the flag
     on → E1 still approved.

   → verify by `docker compose build app && docker compose run --rm app
   pytest`: the full suite is green (410 + 3).
   Traps:
   - `cases.data` must validate as `ReferenceCase`, so build it with
     `ReferenceCase(title=Sourced[str](value="T"), basis=…,
     basis_reason=…).model_dump_json()`;
   - `cases.basis` is a real column: set it as well as the JSON;
   - all test data is made up.

## Tests

- `docker compose build app && docker compose run --rm app pytest`: green.
- Manual, in the local portal (#71): an upload source without the flag,
  and a made-up contract uploaded so it lands with `kind='contract'` and
  no case. Tick the flag: an engagement appears in review. Untick it: it
  leaves review.

## Rollback

- Revert the PR. No schema change. Cases it retired stay `rejected`; a
  re-crawl of a changed document, or the flag change applied again, does
  not restore them (see the spec risks).
