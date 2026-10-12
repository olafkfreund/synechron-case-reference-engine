---
status: draft
issue: 191
spec: spec/2026-10-12-191-confluence-rename-title.md
---

# Plan: A renamed Confluence attachment keeps its old title

## Approved decisions

- **`skip_unchanged()` sets the title**, whatever Confluence does with the
  version on a rename.
- **The value is the bare attachment title**, read from the listing. This is
  the same value `ingest()` gets for a downloaded attachment.
- **No extra request**, and the download skip from #46 stays in place.
- **The `where` clause is unchanged:** a row is still touched only when the
  version matches.
- **Rejected:**
  - downloading again when the title changes, which is more code and costs a
    download;
  - relying on Confluence bumping the version, which is unverified;
  - folding the title into `source_version`, which would change what the
    column means and force every attachment to download again once.
- **Out of scope:** S3, SharePoint and pages. Pages already get their title
  through `ingest()` (#162).

## Steps

1. **`tests/test_crawl_confluence.py`, append at the end (after line 456):**
   add `test_renamed_attachment_with_same_version_shows_new_title(cf)`.
   - Call `cf.page("p1")`, then
     `cf.attach("p1", "a1", "spec.docx", b"spec bytes", number=1)`, then
     `crawl.crawl_confluence(cf.sid)`.
   - Set `cf.atts["p1"][0]["title"] = "renamed.docx"`, call `offer_again(cf)`,
     and crawl again.
   - Assert that the `title` of `att:p1:a1` in `documents` is
     "renamed.docx". Use an inline
     `db.connect()` select, like `version()` at `:209`.
   - Assert `not any("/download/" in u for u in cf.calls)`. The fake builds
     the download path from the title, so `DL` alone would not prove that
     nothing was downloaded.

   → **Verify** by running
   `docker compose -p refs-engine-191 build -q app && docker compose -p refs-engine-191 run --rm app timeout 300 pytest -q -p no:cacheprovider tests/test_crawl_confluence.py -k renamed_attachment`.
   It fails on the title assert.

   **Traps:**
   - There is no bind mount, so rebuild the image before every test run.
   - Use made-up names only.
2. **`app/ingest.py:137-143`, `skip_unchanged`:**
   - Change the signature to
     `skip_unchanged(source_id: int, external_id: str, source_version: str, title: str) -> bool`.
   - Make the statement start
     `update documents set acl_groups=(...for share), deleted_at=null, title=%s`.
   - Make the parameters `(source_id, title, source_id, external_id, source_version)`.
   - Update the docstring to "...un-withdraw, refresh the groups and the
     title".

   **Traps:**
   - Keep the `for share` subselect and the `where` clause byte for byte.
     The lock order is source, then documents.
   - The placeholder order must match the parameter tuple.
3. **`app/crawl.py:429`, the only caller:** change the call to
   `skip_unchanged(source_id, ext, ver, title)`. `title` is set at `:417`.

   → **Verify** with the step 1 command; it passes now.

   **Traps:**
   - #189 edits the S3 branch of `app/crawl.py` (around `:19-60`) in
     parallel. Touch only line 429.
   - If #189 lands first, rebase onto main before merging. The line number
     may shift, but the call is unique: run `grep -n skip_unchanged app/crawl.py`.
   - `grep -rn skip_unchanged app tests` must show no other caller.

## Tests

- **Step 1's test:** fails on main, passes after step 3.
- **The #46 tests:**
  `docker compose -p refs-engine-191 run --rm app timeout 300 pytest -q -p no:cacheprovider tests/test_crawl_confluence.py`
  must be all green. That includes
  `test_unchanged_attachment_is_not_downloaded_again`,
  `test_unchanged_attachment_withdrawn_then_allowed_comes_back_without_download`
  and `test_null_version_row_downloads_once_then_skips`.
- **The full suite:**
  `docker compose -p refs-engine-191 build -q app && docker compose -p refs-engine-191 run --rm app timeout 900 pytest -q -p no:cacheprovider`
  must be all passed.

## Rollback

Revert the PR's merge commit. There is no schema change and no migration,
and titles written meanwhile are just newer names. The next crawl after a
revert leaves them as they are.
