---
status: approved
issue: 58
author: olafkfreund
---

# Intent: Source access changes apply to documents on save

## Problem

Who can see a document is decided by `documents.acl_groups`. Search, review
and generation all filter on it (`app/review.py` line 23). Every crawler
stamps it with a copy of the source's `acl_groups`, so a document always has
its source's groups. The copy is only refreshed at the end of a crawl that
succeeds. Found in the #36 review (security).

What goes wrong:

- **Removing a group does not remove access.** Saving a source on the admin
  page (`app/sources.py` `update()`) changes only `sources.acl_groups`.
  Documents keep the old groups until the next crawl of that source finishes.
  If that source's crawls keep failing (an expired Confluence token, or the
  SharePoint root-permission check raising), the removed group keeps search,
  review and generate access to every document indefinitely. The accepted lag
  was one day.
- **A crawl already running undoes the change.** Each crawler reads the
  source's groups once, at the start (`app/crawl.py` lines 30, 178 and 348),
  and writes that copy back onto every document at the end (lines 76, 282 and
  484), and onto each document it ingests along the way. A crawl that started
  before the admin saved puts the old groups back when it finishes. Crawls
  can run for hours. This second problem was not in the review.

## Proposed outcome

- When an admin saves a source's access groups, every document from that
  source has the new groups as soon as the save returns, in the same
  transaction.
- No crawl, upload or re-ingest can put back groups the admin has since
  changed: documents always get the source's groups as they are at the time
  of writing, not as they were when the crawl started.
- A removed group loses access immediately, whether or not crawls succeed.

## Affected users and systems

- `app/sources.py` (`update()`), `app/crawl.py` (all three crawlers) and
  `app/ingest.py` (`ingest()`), and their tests.
- Admins who manage sources; every user whose access depends on a group.
- The database only: no schema change is expected.

## Constraints

- Must fail closed: if in doubt, a document has the narrower access.
- Must not change who sees what today, except for the lag being removed.
- Must not slow the save noticeably. One `update` over a source's documents is
  expected, and sources hold thousands of documents, not millions.
- SharePoint and Confluence skip and withdraw items with their own unique
  permissions. That behaviour stays as it is.
- Outputs already downloaded are out of scope: a file someone already has
  can't be taken back.

## Open questions

1. **Audit:** should an ACL change be logged like a data-class change (the
   append-only log from #53)? Proposed: yes, in the same transaction. Removing
   access is a security event an auditor will ask about.
2. **Disabling a source:** should unticking "enabled" also hide its documents?
   Proposed: no, out of scope. Today "enabled" only stops crawling. File a
   follow-up if hiding is wanted.
