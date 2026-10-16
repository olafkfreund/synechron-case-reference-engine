---
status: approved
issue: 191
author: olafkfreund
---

# Intent: A renamed Confluence attachment keeps its old title

## Problem

The Confluence crawler skips the download of an attachment whose version is
unchanged (`app/crawl.py:427-432`, #46). It calls `skip_unchanged()`
(`app/ingest.py:137-143`). That function restores the document and refreshes
its access groups, but it does not touch the title.

#162 made the same-checksum branch of `ingest()` (`app/ingest.py:83-89`)
update the title, so a renamed S3 or SharePoint file shows its new name. A
Confluence attachment never reaches that branch when its version is the same.

I reproduced this on main with a throwaway test. An attachment was crawled as
"spec.docx", renamed to "renamed.docx" with the same version, and crawled
again. The document's title stayed "spec.docx".

A renamed Confluence page is not affected. A page is always passed to
`ingest()` with its current title, and the #162 branch updates it. The same
throwaway test passed for a page.

The old name shows in search results, on the review page and in the source
line of downloads. Nothing leaks, because the access groups are still
refreshed. The title is just stale.

## Proposed outcome

- After a crawl, a Confluence attachment that was renamed shows its new name,
  whether or not its version changed.
- An unchanged attachment is still not downloaded again (#46 keeps working).

## Affected users and systems

- `app/ingest.py` (`skip_unchanged`, which has only this one caller) and
  `app/crawl.py` (the Confluence attachment branch).
- Bid writers and reviewers, who see the title.

## Constraints

- No extra request to Confluence. The title is already in the attachment
  listing the crawler fetches.
- Leave S3, SharePoint and pages unchanged.
- **Overlap with #189:** `skip_unchanged()` also clears `deleted_at`, which is
  the restore #189 looks at. Keep this change to the title, so the two don't
  conflict.

## Open questions

1. **Does Confluence bump an attachment's version when it is renamed?** If
   Cloud and Data Center both do, the attachment is downloaded again, the
   #162 branch updates the title, and this issue can't happen in practice.
   The fake in the tests doesn't settle it. Setting the title in
   `skip_unchanged()` is one column in one statement, so I recommend doing it
   anyway rather than depending on how Confluence behaves. The staging crawl
   (#31) can confirm what Confluence actually does.

**Recommendation (approving this intent accepts it):** set the title in skip_unchanged() regardless of whether Confluence bumps the version on a rename; the staging crawl (#31) shows the real behaviour.
