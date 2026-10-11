---
status: approved
issue: 161
author: olafkfreund
---

# Intent: a file the crawl now skips is not left live on its old version

## Problem

All three crawlers in `app/crawl.py` skip a file that is the wrong type or
over the size cap. They check from the listing first, then again after
download. The skip just counts it and moves on:

- **S3:** the key is added to `seen` before the filters, so the deletion
  sweep doesn't withdraw it either.
- **SharePoint:** `handle` returns without calling `withdraw`.
- **Confluence attachments:** the loop continues without calling
  `withdraw`.

The document we already hold for that item stays live: its old text, and
any approved case built from it. Suppose an owner replaces a referenced
deck with a 60 MB version that drops a case study, or renames
`deck.pptx` to `deck.key`. The old version stays searchable and
approvable indefinitely. That breaks the rule `ingest()` follows
everywhere else: a changed document reopens or retires its case.

## Proposed outcome

- When a crawl skips an item for type or size, and we already hold a live
  document for that item, the document is withdrawn. Its case leaves
  search through the existing `deleted_at` rule.
- If the item later fits again, the next crawl brings it back, as for any
  withdrawn document that reappears.
- An item we never held is just counted as skipped, as today.

## Affected users and systems

- Document owners and searchers.
- Admins who change a source's allowed types or size cap.
- `app/crawl.py` (`crawl_s3`, `crawl_sharepoint`, `crawl_confluence`).

## Constraints

- Use each crawler's existing way to withdraw: the `withdraw` helpers, and
  for S3 the `seen` sweep. Don't add a new path.
- The skip counts on the source page stay as they are.
- A skipped file is still never downloaded when the listing alone decides
  the skip.

## Open questions

1. **Does narrowing a source's settings withdraw what is already live?**
   With this fix, an admin who removes `pdf` from a source's types, or
   lowers its size cap, withdraws every matching document on the next
   crawl.
   - **A. Yes.** A document the source would no longer take is not live.
     This is one rule for a changed file and a changed setting alike.
   - **B. No.** Withdraw only when the item itself changed. That needs the
     previous size, name or version stored for each document, and S3
     doesn't give a reliable "changed" signal for a skipped key.

   **Recommendation: A.** It is the simpler rule and fails closed, and
   narrowing a source is an explicit admin act. The source page's help
   text should say so.

**Decision (approved by olafkfreund, 2026-10-10): A.**
