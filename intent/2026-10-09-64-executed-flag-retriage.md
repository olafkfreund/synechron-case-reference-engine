---
status: draft
issue: 64
author: olafkfreund
---

# Intent: Apply a source's "contracts executed" change to contracts already crawled

## Problem

An admin ticks **Contracts executed** on a source (`sources.config ->
'executed_contracts'`, set by `POST /admin/sources/{sid}`,
`app/sources.py:74-96`). It says every statement of work or change order in
that source is signed, so each one becomes an engagement reference without
the signature check (`basis_for`, `app/ingest.py:53-59`).

The flag is only read during `ingest()`, and `ingest()` returns early when a
document's checksum hasn't changed (`app/ingest.py:69-73`). So a change only
reaches documents crawled or changed afterwards:

- **Turned on:** contracts that were already crawled (`documents.kind =
  'contract'`, no case) never get an engagement reference.
- **Turned off:** engagements created because of the flag (`basis =
  'engagement'`, `data->>'basis_reason' = 'source marked executed'`) stay
  open for review or approval, and approved ones stay in search.

The sources page help text (`app/templates/sources.html:5`) states the
limitation instead of fixing it.

## Proposed outcome

When an admin saves a source and the flag actually changes:

- **On:** each of the source's live contract documents with no case gets an
  `extract` job as an engagement (`basis_reason = 'source marked
  executed'`), as if it had just been crawled with the flag on. They then
  show up in review.
- **Off:** each of the source's engagements with reason `source marked
  executed` is retired (`status = 'rejected'`, the same way `ingest()`
  retires a case that no longer qualifies). It leaves review and search.
  Engagements whose own document was triaged as executed (`executed
  contract`) are kept.
- Saving without changing the flag does nothing new.
- The help text no longer states the limitation.
- Tests cover both directions, and a save that doesn't change the flag.

## Affected users and systems

- Admins on the sources page; reviewers (cases appear or leave review);
  search users (retired cases leave results).
- `app/sources.py` (the update route), possibly `app/ingest.py` (a small
  shared helper), `app/templates/sources.html`, `tests/`.
- Not: the schema, extraction, triage, infra.

## Constraints

- In the same transaction as the flag change, so the flag and the
  re-queue or retire can't diverge.
- No LLM call in the admin request. Extraction runs in the worker, as
  usual. Only documents already stored are used (`documents.text`), with no
  re-crawl from S3 or SharePoint.
- Confidential data stays on the source's allowed models: the extract job
  is the same one `ingest()` queues, so the existing model routing applies.
- Deleted documents (`deleted_at`) are skipped.
- No duplicate `extract` jobs if the flag is toggled on twice before the
  worker runs.

## Open questions

1. **Re-run triage or use the stored kind?** `ingest()` keeps only
   `documents.kind`, not triage's `executed` answer. With the flag on, the
   answer doesn't matter: every contract becomes an engagement. So I lean
   towards using `kind = 'contract'` as stored, with no new triage. The
   issue says "re-queue triage", but the outcome is the same, and there's
   no LLM call or new job type.
2. **Contracts whose case was rejected.** Turning the flag on could also
   re-open contracts whose case is `rejected`. But a reviewer's rejection
   and an `ingest()` retirement look the same today. I lean towards
   **only contracts with no case at all**, so a reviewer's rejection is
   never undone.
3. **Approved engagements when the flag goes off.** Retire them too (the
   issue says so), or only open ones? I lean towards all of them: the
   admin has said the source's contracts can no longer be assumed signed.
