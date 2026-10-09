---
status: draft
issue: 46
author: olafkfreund
---

# Intent: Skip unchanged Confluence attachments before downloading them

## Problem

The Confluence crawler downloads an attachment before it knows whether the
attachment has changed. It only finds out afterwards, from the checksum.

- `crawl_confluence()` (`app/crawl.py` lines 393-425): `do_page()` lists all
  of a page's attachments (line 405, already with `expand=version`). It
  downloads each one in full (line 416, up to `CONFLUENCE_MAX_BYTES`,
  default 50 MB). Then `ingest()` (`app/ingest.py` lines 62-73) hashes the
  bytes and returns `skipped` when the checksum matches the stored one.
- The CQL cursor has 1 day of slack (`CURSOR_SLACK`, `app/crawl.py`
  line 16; applied at line 441). So every page modified in the day before
  the cursor is crawled again on the next run, with all of its
  attachments.
- The issue understates the scope. Outside the slack, any edit to a page,
  or a new attachment on it (line 447: an attachment hit crawls its whole
  page), downloads every attachment on that page again. That includes the
  ones that did not change.

The checksum skip keeps Docling and the LLM out of it. Even so, each repeat
costs a full download from Confluence, worker time, and requests against
Confluence throttling (`Confluence.get` retries on 429/503, lines 316-327).
Nothing is stored that would let the crawler skip a download: `documents`
(`sql/schema.sql` lines 25-42) has `checksum` but no source version.

## Proposed outcome

- An attachment whose Confluence version has not changed since it was last
  ingested is not downloaded. It is counted as `skipped`, as today.
- A new attachment, or one with a changed version, is downloaded and
  ingested as today.
- An unchanged attachment that was withdrawn ends up exactly as it would
  after today's checksum skip: un-withdrawn and with the source's current
  ACL groups. That covers a page that was restricted and then allowed
  again, or an attachment that failed on the last run. It is also still
  marked done for this run, so the re-check pass (lines 471-486) does not
  treat it differently.
- A test proves that a second crawl over the same attachment (same
  version) makes no download request.

## Affected users and systems

- `app/crawl.py` `crawl_confluence()` / `do_page()`.
- Possibly `app/ingest.py` `ingest()` and `sql/schema.sql` (`documents`), if
  the version is stored in a column.
- The worker on ECS, and the Confluence instance it crawls (fewer large
  downloads).
- `tests/test_crawl_confluence.py`.
- SharePoint and S3 crawls are not in scope.

## Constraints

- ACL fails closed, as it does today. A skip by version must never leave a
  document searchable when its page, an ancestor or a folder now restricts
  reading. The `allowed()` check runs before any skip, as it does now.
- A wrong skip must not hide a real change. When the version is missing or
  can't be read, the attachment is downloaded (today's behaviour).
- Schema changes are additive and idempotent (`if not exists`), as
  `app/db.py` applies them. Existing rows with no stored version simply
  download once more.
- Confidential documents are never sent to a third-party model. This change
  sends nothing new anywhere.
- Test data is made up: `httpx.MockTransport` fixtures, no real Confluence
  content.
- No new dependency.
- Tests pass with `docker compose build app && docker compose run --rm app pytest`.
- Do not stop or restart the dev stack (`docker compose up`/`down`).

## Open questions

1. **Where to keep the last ingested version.**
   (a) A new nullable `documents.source_version text` column, written when
   the crawler ingests. Simple and per document. Any crawler could use it
   later.
   (b) Infer it from the cursor: skip when `version.when` is at or before
   the previous run's cursor. No schema change, but it is wrong for items
   that failed and are retried by page id (`retry_ids`, lines 459-469).
   Those items have old timestamps and still need the download.
   (c) Keep a map in `sources.last_counts`. That JSON blob grows with every
   attachment. **Lean: (a).**
2. **What counts as "the same version".** The Confluence version number
   alone, or the number plus `version.when` (to be safe after a delete and
   re-upload under the same id)? **Lean: number plus `when`, stored as one
   string.** It costs nothing extra, because `expand=version` already
   returns both.
3. **Should pages get the same skip?** Page bodies come with the CQL hit, so
   there is nothing to download. **Lean: no.** Attachments only, as the
   issue says.
