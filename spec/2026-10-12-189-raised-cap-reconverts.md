---
status: draft
issue: 189
intent: intent/2026-10-12-189-raised-cap-reconverts.md
---

# Spec: An S3 file brought back by a raised size cap returns with the text it had before it changed

## Design

Approved decision: fix S3. For SharePoint and Confluence, add one test each
that shows a withdrawn file is downloaded again when it comes back. Fix them
only if a test fails on main.

### S3 (`app/crawl.py` `crawl_s3`, `:31-81`)

1. **Download keys that were withdrawn.** `known` (`:31-32`) becomes the set
   of keys whose document is live (`deleted_at is null`). The cursor skip
   (`:59-60`) only skips a key in that set. A key that was withdrawn, or
   that is new, is always downloaded. `ingest()` then does what it already
   does:
   - Same bytes: the checksum branch (`app/ingest.py:83-90`) un-withdraws
     the document and keeps its text, which is still correct.
   - Changed bytes: the document is re-converted and re-triaged. Its cases
     are re-queued through the normal changed path (`:115-133`).
2. **Fail closed.** A withdrawn key whose download or ingest fails is held
   in a local `stuck` list, already in `retry`. The sweep's live list
   (`:76-81`) is `seen` minus `stuck`, so that document stays withdrawn and
   the next crawl retries it (`prev_retry`). The `deleted` count is
   unaffected, because those documents are already withdrawn.
3. **Empty-listing guard.** The `if seen or not known` check (`:71`)
   currently asks whether the source has any documents at all. It keeps
   that meaning: it uses a separate `any_docs` flag, because `known` is now
   only the live keys.

Cost: a withdrawn key is downloaded only on the crawl where it comes back
into scope. After that its document is live, so it is skipped again.
Unchanged live keys skip as before.

### SharePoint and Confluence: tests only

- **SharePoint** (`:207-241`): an item is downloaded every time delta
  offers it. There is no version skip. Test: an item is withdrawn as too
  large, then the cap is raised and delta offers it again with new bytes.
  The document is live with the new text.
- **Confluence** (`:426-438`): `skip_unchanged()` (`app/ingest.py:137-143`)
  skips only when the stored `source_version` matches. A change made while
  the attachment was too large bumps the version, so it does not match.
  Test: an attachment is withdrawn as too large, then the cap is raised and
  it is offered again with a new version and new bytes. The document is
  live with the new text.
- `skip_unchanged()` is not touched; #191 is editing it in parallel.

## Alternatives rejected

- **Clear `checksum` when withdrawing for scope**, so the next ingest
  re-converts. This doesn't help: the download is skipped by the cursor
  before `ingest` runs, and the sweep would still un-withdraw the document.
- **Reset the cursor when an admin changes the cap or `include_ext`.** The
  cap is an environment variable that no save sees. A reset would also
  re-download the whole bucket.
- **Leave withdrawn keys out of the sweep's un-withdraw entirely**, and let
  `ingest` revive them. The `seen` list also covers keys that reappear after
  an S3 delete and re-upload. Those are downloaded anyway, because their
  `LastModified` is new, so this would work too. It changes the sweep's
  contract for no gain over the `stuck` list.

## Risks

- **Re-queued cases.** A withdrawn document with changed bytes re-queues its
  cases for extraction. That is the intended behaviour, and it is the same
  as any changed file.
- **Gap left open in SharePoint.** Delta offers an item only when it
  changes. A SharePoint file withdrawn as too large does not come back when
  the cap is raised until it changes again. It never comes back with stale
  text. This is out of scope here, and I'll file it if you agree.
- **Empty-listing guard.** If `known` changes meaning, the guard could start
  treating an empty listing as a mass delete. Step 3 and its test cover
  this.

## Verification

New tests, all in `tests/test_ingest.py` next to
`test_crawl_oversized_existing_doc_is_withdrawn`.

Expected to fail on main:

1. `test_raised_cap_reconverts_a_file_changed_while_too_large`
   - Cap 10 bytes. Ingest "old text", then replace it with a 21-byte body
     (withdrawn).
   - Move the cursor past the slack and raise the cap.
   - The crawl counts 1 `updated`, and the document is live with the new
     text.
   - Main gives counts of 0 and "old text".
2. `test_widened_include_ext_reconverts_a_file_changed_while_excluded`: the
   same flow through `include_ext` instead of the cap.
3. `test_withdrawn_key_that_fails_on_return_stays_withdrawn`: the same
   setup, with `ingest` patched to raise. The document stays withdrawn, and
   the key is in `retry_ids`.

Expected to pass on main and after the change:

4. `test_unchanged_live_key_still_skips_the_download`: guards the cursor
   saving.
5. `test_empty_listing_with_only_withdrawn_documents_is_not_a_mass_delete`:
   guards the empty-listing check.
6. `test_sharepoint_withdrawn_file_returns_with_new_text`. If it fails, fix
   SharePoint and say so in the plan.
7. `test_confluence_withdrawn_attachment_returns_with_new_text`. If it
   fails, fix Confluence and say so in the plan.

The full suite passes in `docker compose -p refs-engine-189 run --rm app pytest -q -p no:cacheprovider`.
