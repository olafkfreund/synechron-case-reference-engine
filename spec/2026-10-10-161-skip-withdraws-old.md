---
status: draft
issue: 161
intent: intent/2026-10-10-161-skip-withdraws-old.md
---

# Spec: an item the crawl now skips for type or size is withdrawn

## Design

Decision A: a document the source would no longer take is not live. This covers
a changed file and a narrowed setting alike. Each crawler uses the withdraw path
it already has.

- **S3, `crawl_s3` (`app/crawl.py`).** Move `seen.append(key)` from before the
  type and size filters to after them. A skipped key is then missing from `seen`.
  The existing sweep at the end sets `deleted_at` on every live document not in
  `seen`, and counts it under `deleted`. A key that fits again is back in `seen`
  on a later crawl, and the same sweep clears `deleted_at`. An unchanged key is
  not downloaded again: its stored text is what it held before.
  The comment "a skipped key is not a deletion" changes to say it is now
  withdrawn (#161). Decisions are still made from the listing alone, so a
  skipped file is never downloaded.
- **SharePoint, `crawl_sharepoint`, `handle()`.** The type branch, the
  listing-size branch and the post-download size branch each call the existing
  `withdraw(iid)` before `return`. `withdraw` only touches a live row, so an item
  we never held is a no-op.
- **Confluence attachments, `crawl_confluence`, `do_page()`.** The type branch,
  the `fileSize` branch and the post-download size branch each call the existing
  `withdraw(ext)` before `continue`.
- **Unchanged:** every `skipped_type` and `skipped_too_large` count increments
  as today. Pages, as opposed to attachments, have no type or size skip.
- **Source page help text (`app/templates/sources.html`).** Next to the file
  types and size settings, add one line: "Narrowing these withdraws matching
  documents already in search on the next crawl." The other two crawlers take
  their cap from env, so only the S3 setting is shown.

## Alternatives rejected

- **B, withdraw only when the item itself changed.** It needs per-document
  stored size, name and version, and S3 gives no reliable change signal for a
  key it skips.
- **A new "skipped" document state.** That adds a third path beside
  `deleted_at`, which the constraints rule out.

## Risks

- **Narrowing a source withdraws documents on its next crawl.** This is the
  decided behaviour, and an admin's explicit act. Approved cases on those
  documents leave search through the existing `deleted_at` rule.
- **S3, all keys skipped.** If every key is skipped, `seen` is empty, and the
  existing empty-listing guard treats the run as a mistake and withdraws
  nothing. This is deliberate: it protects against a mass withdraw caused by a
  typo in `include_ext`. The run records `empty_listing`.
- **SharePoint and Confluence delta crawls** only offer changed items. After an
  admin widens a setting again, a withdrawn item comes back when it next changes
  or on the next full listing. Today the same applies to any withdrawn item.

## Verification

- **Change an existing test,** `tests/test_ingest.py::test_crawl_include_ext_and_oversized_existing_doc_stays_live`.
  Rename it `test_crawl_oversized_existing_doc_is_withdrawn`. After the large
  re-upload it expects `(skipped_too_large, deleted) == (1, 1)`, and the
  document's `deleted_at` is set. On main the test fails at the new assert.
- **Add `test_crawl_s3_type_narrowed_withdraws`** in `tests/test_ingest.py`:
  - Crawl `a.docx` and `b.pdf`.
  - Set `include_ext` to `["docx"]` and crawl again.
  - `b.pdf` is withdrawn and `a.docx` stays live.
- **Add `test_sharepoint_item_grown_past_cap_is_withdrawn`** in
  `tests/test_crawl_sharepoint.py`. A live item whose listing size now exceeds
  the cap is withdrawn and not downloaded.
- **Add `test_confluence_attachment_grown_past_cap_is_withdrawn`** in
  `tests/test_crawl_confluence.py`. The same check for an attachment.
- **Existing tests stay green:**
  - `test_crawl_skips_type_and_size_without_downloading` checks counts and
    downloads. On its second crawl `deleted` stays 0, because those files were
    never held.
  - The empty-listing tests.

All new tests must fail on main. Then the full suite: `docker compose build app &&
docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`.
