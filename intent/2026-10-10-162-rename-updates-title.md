---
status: approved
issue: 162
author: olafkfreund
---

# Intent: Renaming a document without changing its content leaves the old title shown

## Problem

When a crawl offers a document whose bytes are unchanged, `ingest`
(`app/ingest.py:83-87`) takes the same-checksum path. That path updates
`acl_groups`, `deleted_at` and `source_version`, but not `title`.

The title can change while the bytes stay the same:

- **SharePoint.** A renamed file keeps its item id and its bytes. The crawl
  passes the new name (`app/crawl.py:231`), and `ingest` drops it.
- **Confluence.** The checksum covers only the page body (`app/crawl.py:409`).
  If an owner retitles a page but leaves its body alone, the new title is
  dropped.
- **S3.** Not affected. A renamed key is a new `external_id`, so it is a new
  document.

The stale title shows on the review list and the review page (`app/review.py`,
lines 119, 123 and 137, which read `d.title`). An owner may have renamed a file
to remove a client's or project's name, and reviewers keep seeing it. Search
results are not affected, because they show the case's extracted title.

## Proposed outcome

- After a crawl, a document's title matches the source's current name, even
  when its content is unchanged.
- A same-bytes rename does not re-triage the document, re-extract it or reopen
  its case. It stays a cheap skip.

## Affected users and systems

- `app/ingest.py` (`ingest`, same-checksum path).
- Reviewers, on the review list and review page.
- SharePoint and Confluence sources.

## Constraints

- No extra model call, conversion or S3 write for a same-bytes document.
- `ingest` still returns `"skipped"` for it, so crawl counts don't change.
- Public repo: made-up names only in tests.

## Open questions

None.
