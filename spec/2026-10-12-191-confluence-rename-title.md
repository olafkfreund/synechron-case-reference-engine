---
status: draft
issue: 191
intent: intent/2026-10-12-191-confluence-rename-title.md
---

# Spec: A renamed Confluence attachment keeps its old title

## Design

Approved in the intent: `skip_unchanged()` sets the title, whatever
Confluence does with the version on a rename.

1. **`app/ingest.py:137-143`, `skip_unchanged`:**
   - Add a `title` parameter: `skip_unchanged(source_id, external_id, source_version, title)`.
   - Add `title=%s` to the `update documents set ...` statement, next to
     `deleted_at=null`.
   - Update the docstring to "...un-withdraw, refresh the groups and the
     title".
   - The `where` clause is unchanged, so a row is still only touched when the
     version matches.
2. **`app/crawl.py:429`, the only caller:** pass `title`, which is already
   read from the listing at `:417` and has passed the type filter at `:418`.
   This means no extra request.
3. **The value is the bare attachment title**, the same value `ingest()` gets
   at `:438`. This keeps the two paths consistent.

## Alternatives rejected

- **Skip the version check when the title differs, so a rename downloads
  again and goes through #162's branch.** That costs a download for a name
  change, and it needs a select of the stored title before the skip. It is
  more code for the same result.
- **Rely on Confluence bumping the version on a rename.** This is unverified
  for both Cloud and Data Center, and the intent's decision was not to depend
  on it.
- **Fold the title into `source_version`.** That would change what the column
  means and force every existing attachment to download again once after the
  release.

## Risks

- **Title length or characters:** none new. The same string already reaches
  `ingest()` and is stored there.
- **#189:** it does not touch `skip_unchanged()` (confirmed by the
  coordinator), so there is no conflict.
- **Restore path:** `skip_unchanged()` also brings back a withdrawn document
  that is allowed again
  (`test_unchanged_attachment_withdrawn_then_allowed_comes_back_without_download`).
  It now gets the current title too, which is correct.

## Verification

- **New test in `tests/test_crawl_confluence.py`,
  `test_renamed_attachment_with_same_version_shows_new_title`:**
  - crawl "spec.docx" with `number=1`;
  - set `cf.atts["p1"][0]["title"] = "renamed.docx"`, then `offer_again(cf)`
    and crawl again;
  - assert the stored title is "renamed.docx", and that `DL` was not fetched
    on the second crawl (no download).

  It fails on main: the same steps were run as a throwaway test while writing
  the intent, and the title stayed "spec.docx".
- **Existing #46 tests stay green:**
  - `test_unchanged_attachment_is_not_downloaded_again`;
  - `test_unchanged_attachment_withdrawn_then_allowed_comes_back_without_download`;
  - `test_null_version_row_downloads_once_then_skips`.
- **The full suite passes:**
  `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`.
