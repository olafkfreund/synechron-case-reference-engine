---
status: draft
issue: 57
author: olafkfreund
---

# Intent: File-share crawler skips unsupported and oversized files

## Problem

The file-share crawler (`crawl_s3` in `app/crawl.py`, fed by DataSync from
`infra/datasync.tf`) downloads every object in the bucket prefix, whole, into
memory before it knows whether the file can be used. The SharePoint and
Confluence crawlers already skip by file type and size from the listing, before
downloading. The file-share crawler does neither. Found in the #36 review.

What goes wrong:

- **One large file stops a share from ever being crawled.** A 9 GB video on a
  share is read into the 8 GB worker, which is killed. The job is reclaimed after
  6 hours and abandoned after 3 attempts. The cursor is only written at the end of
  a crawl, so every rerun starts from scratch and dies on the same file.
- **Unsupported files are re-downloaded on every crawl.** A file that cannot be
  converted never gets a `documents` row, so the next crawl treats it as new,
  downloads it again, copies it to `originals/` in S3 and fails again. Real
  shares hold many such files (images, spreadsheets, archives, legacy `.doc`).
- **DataSync copies the whole share**, so the bucket fills with files we will
  never use.

## Proposed outcome

- A file share with large or unsupported files completes its crawl.
- Files of an unsupported type, or over the size cap, are skipped from the
  listing alone, without downloading, and are counted in the crawl's result
  (`skipped_type`, `skipped_too_large`), as SharePoint does today.
- Nothing unsupported is copied to `originals/`.
- An admin can see the skip counts on the sources page, as for SharePoint.

## Affected users and systems

- `app/crawl.py` `crawl_s3`, and its tests.
- Admins who configure file-share sources (the sources page shows the counts).
- Possibly `infra/datasync.tf` (see open questions).
- The worker on ECS: memory use during a file-share crawl.

## Constraints

- Must match SharePoint's behaviour and settings style: the same default
  extensions (`docx`, `pptx`, `pdf`), an optional per-source `include_ext`, and
  an env-configurable byte cap with the same 50 MB default.
- Must not mark a skipped file as deleted, and must not change how real
  deletions are detected (a skipped key still counts as "seen").
- Must not change the cursor, the advisory lock or the ACL restamp.
- No new dependency.

## Open questions

1. **A document already ingested that grows past the cap, or is renamed to
   an unsupported type:** keep the last good version live, or withdraw it?
   Proposed: keep it. SharePoint keeps it today, and the change only stops
   new downloads.
2. **DataSync include filter:** also add an include filter (`*.docx|*.pptx|*.pdf`)
   to the DataSync task so unused files never reach the bucket? Proposed: yes.
   It is a small Terraform change, but `plan` and `apply` need AWS credentials
   and run at deploy time (#37).
3. **Legacy `.doc`:** stays unsupported, like SharePoint. The presale folder has
   one. Proposed: out of scope, file a follow-up if the bid team needs it.
