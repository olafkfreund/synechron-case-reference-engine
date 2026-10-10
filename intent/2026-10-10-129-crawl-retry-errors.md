---
status: approved
issue: 129
author: olafkfreund
---

# Intent: A non-HTTP error on one retried item fails the whole SharePoint or Confluence crawl, every run

## Problem

The crawlers catch per-item failures so that one bad file doesn't stop the
crawl. Three paths catch only the crawler's own HTTP error class
(`GraphError` or `ConfluenceError`):

- the SharePoint `retry_ids` loop (`app/crawl.py:255-262`);
- the Confluence `retry_ids` loop (`:465-474`);
- Confluence's by-id fetch for an attachment's page (`:455-459`).

An `httpx` timeout or connection error on one of these escapes and fails the
crawl job. The cursor and `retry_ids` are kept, so the next crawl retries the
same item first. An item that times out every time, such as a very large page
behind a slow proxy, then blocks the whole source on every run. Nothing new
is ingested, and no access is re-checked.

## Proposed outcome

- A transport error on one retried or by-id item is counted as a failure.
- The item stays on the retry list.
- The crawl goes on, as it already does for the same error elsewhere.

## Affected users and systems

- `app/crawl.py` (`crawl_sharepoint`, `crawl_confluence`).
- Tests: `tests/test_crawl_sharepoint.py`, `tests/test_crawl_confluence.py`.

## Constraints

- A 404 still withdraws the item, as today.
- The error type is recorded, but not the message, which can quote paths.
- Errors in enumeration (delta or CQL paging) still fail the crawl and keep
  the cursor. Only the per-item paths change.

## Open questions

None. The per-item `except` in these three places widens to `Exception`, and
the 404 branch is kept.

## Approved answers

None needed.
