---
status: approved
issue: 121
author: olafkfreund
---

# Intent: S3 crawl loses a new version of a known key once its ingest fails and the cursor moves on

## Problem

`crawl_s3` (`app/crawl.py:30-66`) skips a key it already knows when its
`LastModified` is older than the cursor minus one day (`CURSOR_SLACK`). The
cursor moves to the newest `LastModified` it has seen, and it moves before the
download is attempted.

An updated version of a known key that fails to ingest (Docling, S3 read or
model error) is retried on the next crawls only while it stays inside the
one-day slack. Once other uploads move the cursor more than a day past it, the
key is skipped without a download, and the failure is lost:

- the old version stays searchable;
- the new version never arrives;
- nothing is shown.

`failed_keys` is written to `last_counts`, but only for display; nothing reads
it back. The SharePoint and Confluence crawlers solve the same problem with
`retry_ids` (`app/crawl.py:255`, `:465`): the IDs that failed last run are
retried next run, whatever the cursor says. S3 has no equivalent.

## Proposed outcome

- A key that failed to ingest is downloaded again on every later crawl until it
  succeeds or disappears from the bucket, whatever the cursor.
- Keys that succeeded are skipped exactly as today.
- S3 behaves like SharePoint and Confluence: the same `retry_ids`-style
  record in `last_counts`.

## Affected users and systems

- `app/crawl.py` (`crawl_s3`), and `sources.last_counts` (JSON contents only,
  no schema change).
- Upload sources (#96, #110) go through `crawl_s3` too, so they are covered.
- Tests: `tests/test_crawl.py` (moto S3).
- Not affected: the SharePoint, Confluence and file-share crawlers.

## Constraints

- Follow the SharePoint and Confluence pattern, not a new mechanism.
- Retry all failed keys, not only the display-capped `failed_keys`
  (`MAX_FAILED_KEYS`), so none is lost.
- Store no error messages. Keys only, as today.

## Open questions

1. **Cap the size of the stored retry list?** SharePoint and Confluence store
   every failed ID with no cap. **Recommendation: no cap, the same as them.**
   The list is only as long as the number of failing files.

## Approved answers

1. No cap on the stored retry list, the same as SharePoint and Confluence.
