---
status: approved
issue: 206
author: olafkfreund
---

# Intent: Raising the SharePoint size cap brings back the files it skipped

## Problem

A SharePoint file larger than `SHAREPOINT_MAX_BYTES` (50 MB by default) is
counted as `skipped_too_large` and withdrawn (`app/crawl.py`, `handle()`, the two
size branches). Graph delta only offers a file again when it changes. So if an
admin raises the cap, a file that was skipped and has not changed since is
never fetched again, and it stays out of search.

#189 fixed the other half: a skipped file that later changes comes back
(`test_sharepoint_withdrawn_file_returns_with_new_text`). S3 has no gap,
because it lists the whole bucket on every crawl. The only workaround today is
to reset the drive's delta cursor, which forces a full resync of the whole
drive.

## Proposed outcome

- After the cap is raised, the next SharePoint crawl fetches every file that an
  earlier crawl skipped as too large and that now fits, without the file
  changing and without a cursor reset. It goes live with its text, under the
  usual type, permission and anonymisation checks.
- A file still over the cap stays withdrawn and counted as `skipped_too_large`.
- A skipped file deleted in the meantime is withdrawn, not retried forever.
- Crawls where the cap has not changed do no extra Graph calls for these files.

## Affected users and systems

- Admins who raise `SHAREPOINT_MAX_BYTES`. Bid users then find those cases.
- `app/crawl.py` (`crawl_sharepoint`), the per-source crawl state in
  `sources.last` (where `retry_ids` lives), and `tests/test_crawl_sharepoint.py`.

## Constraints

- Fail closed as today: a file that cannot be verified, or has its own
  permissions, is withdrawn and never left live.
- No schema migration if the existing per-source state JSON can hold it.
- The delta cursor must not be reset or lost.
- No AWS or Bedrock needed: the SharePoint tests use a fake Graph.

## Open questions

- Does Confluence have the same gap for an attachment skipped by
  `CONFLUENCE_MAX_BYTES`? Its crawl re-checks live documents, not withdrawn
  ones. The spec checks this and either includes Confluence or files a separate
  issue.
