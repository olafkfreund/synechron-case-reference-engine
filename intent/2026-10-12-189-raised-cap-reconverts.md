---
status: approved
issue: 189
author: olafkfreund
---

# Intent: An S3 file brought back by a raised size cap returns with the text it had before it changed

## Problem

`crawl_s3` (`app/crawl.py:19-86`) leaves out an object that is over
`S3_MAX_BYTES` or not in `include_ext`. Since #161, the sweep then withdraws
a document whose key it left out. That part works.

When the object comes back into scope, it returns with old text:

1. An object is ingested as "old text".
2. It is replaced with a body over the cap, and the crawl withdraws it. The
   stored text and checksum are still those of the old version.
3. More than a day later, an admin raises the cap (or widens
   `include_ext`).
4. The next crawl lists the key. Its `LastModified` is behind the cursor
   slack and the key is known, so the download is skipped (`:59-60`).
5. The sweep sets `deleted_at = null` for every listed key (`:76-81`). The
   document is live again, searchable on its old text, with its old cases.
   It is never re-converted, re-triaged or re-extracted.

I reproduced this on main: every count is 0, and the document is live with
the text "old text".

The same path brings back a type that was excluded and then included again.
If the bytes did not change while the object was out of scope, the old text
is still correct. The bug is a change made while it was out of scope.

## Proposed outcome

- A document that the crawl withdrew because it was out of scope is
  downloaded again when it comes back into scope. If its bytes changed, it is
  re-converted and re-triaged like any changed file. If they did not change,
  it comes back as now, through the checksum skip.
- A key that stays in scope and has not changed still skips the download.
  The cursor saving is kept.

## Affected users and systems

- `app/crawl.py` `crawl_s3`, and its sweep.
- Bid team: stops seeing facts from a version the source no longer holds.
- Reviewers: the changed file's cases come back through the normal changed
  path.
- File-share sources: they reach S3 through DataSync, so this applies to
  them too.

## Constraints

- No extra downloads for keys that are live and unchanged. The daily crawl
  must stay cheap on large buckets.
- Lock order stays source, then documents, then cases.
- Fail closed: if the re-download fails, the document stays withdrawn and is
  retried. It must never go live on its old text.

## Open questions

- SharePoint and Confluence withdraw in their skip branches too, but they
  decide on a source version that changes with the content. I expect them to
  re-download, but I have not checked. Should this issue cover them, or only
  S3?

**Decided by the user, 2026-10-12:** fix S3, and add one test each for SharePoint and Confluence showing they already download a withdrawn file again; fix those only if a test fails.
