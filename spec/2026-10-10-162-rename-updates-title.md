---
status: draft
issue: 162
intent: intent/2026-10-10-162-rename-updates-title.md
---

# Spec: A same-bytes rename updates the document title

## Design

`ingest()` in `app/ingest.py`, the same-checksum branch: add `title=%s` to the
existing `update documents set ...` and pass `title` with the other values.

- The branch still returns `"skipped"`. No S3 put, no conversion, no triage,
  no extract job, and the case is not reopened: only the title row changes.
- Every crawler already passes its current name as `title` (S3 key name,
  SharePoint item name, Confluence page or attachment title), so no crawler
  changes.
- Review list and page read `d.title`, so they show the new name on the next
  load.

## Alternatives rejected

- **Treat a rename as a new version** (re-triage and reopen the case): costs a
  conversion and a model call for no content change, and would send a reviewed
  case back to the queue for a filename.
- **Include the title in the checksum**: same cost, and changes every stored
  checksum, so the first crawl after deploy would re-ingest everything.

## Risks

- A crawler that passed an empty or placeholder title would now overwrite a
  good one. None does today; `ingest` callers all pass the source's name.
- The search index does not read `documents.title`, so search is unchanged.

## Verification

- New `tests/test_ingest.py::test_same_bytes_rename_updates_title`: ingest
  `b"one"` as "old.docx", then the same bytes as "new.docx"; the second call
  returns `"skipped"`, the document's title is "new.docx", and no extract job
  was queued by the second call. Fails on main (title stays "old.docx").
- Full suite green.
