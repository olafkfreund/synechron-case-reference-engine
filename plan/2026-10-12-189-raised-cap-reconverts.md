---
status: approved
issue: 189
spec: spec/2026-10-12-189-raised-cap-reconverts.md
---

# Plan: An S3 file brought back by a raised size cap returns with the text it had before it changed

## The bug

`crawl_s3` skips downloading a key it already knows when that key's
`LastModified` is behind the cursor slack. The sweep then marks every key
in the listing live (`deleted_at = null`).

So an object that changed while it was out of scope comes back on its old
text once it is in scope again. Out of scope means over `S3_MAX_BYTES` or
not in `include_ext`.

## Approved decisions

1. **Download withdrawn keys again.**
   - `known` holds only the keys whose document is live (`deleted_at is
     null`), and the cursor skip applies only to those keys.
   - A key that was withdrawn, or is new, is always downloaded.
   - `ingest()` handles the rest:
     - same bytes: the checksum branch un-withdraws the document and keeps
       its text;
     - changed bytes: it re-converts, re-triages and re-queues the cases.
2. **Fail closed.**
   - A withdrawn key whose download or ingest fails goes into a local
     `stuck` set. It is also in `retry` already.
   - The sweep's live list is `seen` minus `stuck`, so that document stays
     withdrawn and the next crawl retries it.
   - The `deleted` count is unchanged, because those documents are already
     withdrawn.
3. **Keep the empty-listing guard's meaning.** The guard still asks whether
   the source has any documents at all, through a separate `any_docs` flag.
4. **SharePoint and Confluence get tests only.** Add one test each showing
   a withdrawn file is downloaded again when it comes back. Fix the crawler
   only if its test fails on main, and record that as a deviation here.
5. **Don't touch `skip_unchanged()`** (`app/ingest.py:137-143`). #191 is
   editing it in parallel.
6. **Out of scope:**
   - SharePoint delta offers an item only when it changes, so a file
     withdrawn for size waits for its next change to come back. It never
     comes back with stale text.
   - Resetting the cursor on a cap change.
   - Clearing checksums.

## Steps

### Step 1. Tests (`tests/test_ingest.py`)

Add the tests after `test_crawl_s3_type_narrowed_withdraws`, around line 300.

**Pattern to follow:** `test_crawl_oversized_existing_doc_is_withdrawn`
(`:274-286`). Use the `set_cursor(sid, iso)` helper (`:313`) to put the
cursor 3 days in the future. That makes the stored keys older than the
slack.

**Expected to fail on main:**

- **`test_raised_cap_reconverts_a_file_changed_while_too_large`**
  1. Set `include_ext` to `["txt"]` and `S3_MAX_BYTES` to 10.
  2. Put `in/a.txt` = `b"old text"` and crawl: `new` is 1.
  3. Put `b"brand new longer text"` and crawl: the document is withdrawn.
  4. Set the cursor to now + 3 days, raise `S3_MAX_BYTES` to 1000, and
     crawl.
  5. Expect `updated == 1`, the document live, and text containing
     "brand new".
- **`test_widened_include_ext_reconverts_a_file_changed_while_excluded`**
  1. Ingest `in/b.pdf` = `b"old"`.
  2. Set `include_ext` to `["docx"]`, put `b"changed"`, and crawl: the
     document is withdrawn.
  3. Move the cursor, set `include_ext` back to `["docx","pdf"]`, and
     crawl.
  4. Expect the document live with the new text.
  5. If the PDF stub conversion can't take these bytes, use `txt` instead.
- **`test_withdrawn_key_that_fails_on_return_stays_withdrawn`**
  1. Use the same setup as the first test.
  2. Before the last crawl, monkeypatch `crawl.ingest` to raise.
  3. Expect the document still withdrawn, `"in/a.txt"` in
     `last_counts->'retry_ids'`, and `failed == 1`.

**Expected to pass on main and after the change:**

- **`test_unchanged_live_key_still_skips_the_download`**
  1. Ingest a file, move the cursor, then wrap `s3.get_object` with a
     counter. (Or reuse the call-counting approach of
     `test_cursor_skips_known_old_keys_but_fetches_unknown`, near `:318`.)
  2. Expect no `get_object` call for that key.
- **`test_empty_listing_with_only_withdrawn_documents_is_not_a_mass_delete`**
  1. Ingest a file, then narrow `include_ext` so the file is withdrawn.
  2. Delete the object from the bucket, so the listing is empty.
  3. Crawl again and expect `counts["empty_listing"] is True`.

**Verify:** run `bash $SCRATCH/check.sh ../refs-engine-189 "tests/test_ingest.py"`. The three expected-failure tests must fail on main.

**Traps:**
- There is no bind mount. Run `docker compose -p refs-engine-189 build -q app`
  before every test run.
- Use made-up names only.

### Step 2. Fix `app/crawl.py` `crawl_s3` (`:31-81`)

1. **Lines 31-32:** replace `known` with two queries over the same rows:
   ```python
   rows = lock.execute("select external_id, deleted_at is null from documents where source_id=%s", (source_id,)).fetchall()
   known = {k for k, live in rows if live}   # only live keys may be skipped on the cursor (#189)
   any_docs = bool(rows)
   ```
2. **Line 46:** `failed, retry, stuck = [], [], set()`.
3. **Line 59:** the cursor skip stays as written. `known` now means live
   keys only.
4. **Inside the `except`, around line 66:** add `stuck.add(key)`.
   - The sweep only matters for a key that has a document. A new key that
     fails has none, so adding it does no harm.
   - A live key that fails keeps its old version live, as it does today,
     because `stuck` only keeps withdrawn documents withdrawn.
   - The update uses `coalesce(deleted_at, now())`. Leaving a live key out
     of the list would withdraw it, so only add keys that are not in
     `known`: `if key not in known: stuck.add(key)`.
5. **Line 71:** change the guard to `if seen or not any_docs:`.
6. **Lines 72-81:** pass `live = [k for k in seen if k not in stuck]` to
   both the count query and the update, in place of `seen`.
   - The count is unchanged, because stuck documents already have
     `deleted_at` set.
   - The update keeps them withdrawn (`coalesce(deleted_at, now())` keeps
     the old timestamp).

**Verify:** run the step 1 tests. All pass.

**Traps:**
- Lock order is source, then documents, then cases. The sweep's
  `for share` on the source runs before its documents update, as now. Add
  no new lock.
- `retry_ids` must still include stuck keys, so that `prev_retry` forces
  the next try.
- Don't restructure `ingest()`.

### Step 3. Tests for SharePoint and Confluence

Add one test to `tests/test_crawl_sharepoint.py` and one to
`tests/test_crawl_confluence.py`.

- **`test_sharepoint_withdrawn_file_returns_with_new_text`**
  1. Use the `f(iid, size=...)` and `page(...)` helpers and set
     `SHAREPOINT_MAX_BYTES`.
  2. Ingest, then have delta offer the item over the cap: it is withdrawn.
  3. Raise the cap and have delta offer it again with new bytes. Expect it
     live with the new text.
- **`test_confluence_withdrawn_attachment_returns_with_new_text`**
  1. Model it on `test_confluence_attachment_grown_past_cap_is_withdrawn`
     (`:229`) and `offer_again` (`:214`), with `CONFLUENCE_MAX_BYTES`.
  2. Raise the cap and offer the attachment again with a new version and
     new bytes. Expect it live with the new text.

**Verify:** run both files. Both are expected to pass on main. If one
fails, fix that crawler with the smallest change, and record it under
Deviations here in the same commit.

**Traps:**
- `skip_unchanged()` belongs to #191. Don't edit it. If the Confluence fix
  seems to need it, stop and report.

### Step 4. Full suite and help text

1. Run the full suite in the worktree.
2. Grep `docs/user-guide/admins.md` for "size" and "include_ext". If it
   says a withdrawn file "comes back", check that the wording still holds.
   Edit only if it is now wrong.

**Verify:**
```
docker compose -p refs-engine-189 build -q app && docker compose -p refs-engine-189 run --rm app timeout 900 pytest -q -p no:cacheprovider
```

## Tests

- Step 1: three tests fail on main and pass after step 2. Two pass on both.
- Step 3: both tests pass on main. If either fails, record it as a
  deviation and fix it.
- The full suite passes. The current count is 653 plus the new tests.

## Rollback

Revert the merge commit. There is no schema or data change. Documents that
came back with new text stay correct after a revert.

## Deviations

None yet.
