---
status: approved
issue: 161
spec: spec/2026-10-10-161-skip-withdraws-old.md
---

# Plan: an item the crawl now skips for type or size is withdrawn

Approved decision (A): a document the source would no longer take is not live.
This covers a changed file and a narrowed setting alike. Each crawler withdraws
through the path it already has:

- **S3:** the `seen` sweep. A skipped key is left out of `seen`.
- **SharePoint:** the existing `withdraw(iid)`.
- **Confluence attachments:** the existing `withdraw(ext)`.

The following stay as they are:

- The `skipped_type` and `skipped_too_large` counts. No crawler gets new counts.
- When the listing alone decides a skip, the file is still never downloaded.
- An item we never held is only counted. Both `withdraw` helpers touch only a
  live row, and the S3 sweep only touches documents we hold.
- An item that fits again comes back the way any withdrawn item does:
  - **S3:** it is in `seen` again, and the sweep clears `deleted_at`.
  - **SharePoint and Confluence:** it comes back when delta or CQL offers it
    again.
- The S3 empty-listing guard. If every key is skipped, `seen` is empty, nothing
  is withdrawn and the run records `empty_listing`. This protects against a
  typo in `include_ext`.

Rejected alternatives:

- **B, withdraw only when the item itself changed.** It needs a stored
  size, name and version for each document, and S3 gives no change signal.
- **A new "skipped" document state.** It would add a third path beside
  `deleted_at`.

Coder handoff: yes. Five steps edit files, in six files.

## Steps

1. **Tests first.**
   - **`tests/test_ingest.py:196-209`:**
     - Rename `test_crawl_include_ext_and_oversized_existing_doc_stays_live`
       to `test_crawl_oversized_existing_doc_is_withdrawn`.
     - After the large re-upload, assert
       `(counts["skipped_too_large"], counts["deleted"]) == (1, 1)`.
     - The final select becomes `deleted_at is not null` for `in/a.docx` and
       must be true.
     - Keep the `include_ext` set to `["txt", "docx"]`: `notes.txt` stays
       live, so `seen` is not empty.
   - **`tests/test_ingest.py`, new, right after it:**
     `test_crawl_s3_type_narrowed_withdraws(env)`:
     - Put `in/a.docx` and `in/b.pdf`, crawl, and expect `new == 2`.
     - Run
       `update sources set config = config || '{"include_ext": ["docx"]}' where id=%s`.
     - Crawl again. `deleted == 1`, `skipped_type == 1`, `in/b.pdf` has
       `deleted_at` set and `in/a.docx` has it null.
   - **`tests/test_crawl_sharepoint.py`, new, after `test_type_and_size_filters` (line 220):**
     `test_sharepoint_item_grown_past_cap_is_withdrawn(sp)`:
     - Run `first_run(sp)`, so `f1` and `f2` are live.
     - Set `sp.delta[f"{ROOT}?token=d1"] = (200, page([f("f1", size=10**9)], delta=f"{ROOT}?token=d2"))`.
     - Run `sp.calls.clear()` and crawl.
     - Assert `skipped_too_large == 1`, `docs(sp.sid)["f1"][0] is True` (that
       is, `deleted_at` is set), `docs(sp.sid)["f2"][0] is False`, and
       `sp.content_calls() == []`.
   - **`tests/test_crawl_confluence.py`, new, after `test_attachment_filters` (line 196):**
     `test_confluence_attachment_grown_past_cap_is_withdrawn(cf)`:
     - Run `cf.page("p1")`, `cf.attach("p1", "a1", "spec.docx", b"spec bytes")`
       and crawl. `att:p1:a1` is in `live(cf.sid)`.
     - Run
       `cf.atts["p1"][0]["extensions"]["fileSize"] = 10**9; offer_again(cf)`.
       Move the test below `offer_again` (line 214) or call
       `cf.pages["p1"]["version"]["when"] = "2026-02-01T00:00:00.000Z"; cf.calls.clear()`
       inline.
     - Crawl. Assert `att:p1:a1` is not in `live(cf.sid)`, `page:p1` still
       is, and `DL not in cf.calls`.
   - Verify:
     `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_ingest.py tests/test_crawl_sharepoint.py tests/test_crawl_confluence.py`.
     On main code all four of these tests fail, and every other test passes.
   - Traps:
     - There is no bind mount, so always build before running tests.
     - Never run `docker compose up` or `down`.
     - Use made-up names only.
     - Leave `test_crawl_skips_type_and_size_without_downloading` unchanged.
       Its second crawl still expects `deleted == 0`, because those files
       were never held.
     - `DL` is defined at `tests/test_crawl_confluence.py:206`, below
       `test_attachment_filters`. A test placed above it would still resolve
       `DL` at run time. Placing the new test after `offer_again` is cleaner.

2. **`app/crawl.py:49`, `crawl_s3()`.**
   - Delete `seen.append(key)` and its comment from line 49.
   - Add `seen.append(key)  # after the filters: a key we now skip is withdrawn by the sweep (#161)`
     directly after the size check (after line 56, before
     `modified = obj["LastModified"]`).
   - Keep the comment on line 50 ("decided from the listing alone…") with
     the filters.
   - Verify: step 1's command. The two `test_ingest.py` tests pass.
   - Traps:
     - `seen` must still include known keys that the cursor check on line 59
       skips. That is why the append goes before the cursor `continue`.
       Otherwise every unchanged key would be swept.
     - Leave the sweep and the empty-listing guard (lines 70-85) alone.

3. **`app/crawl.py:207-231`, `crawl_sharepoint()`, `handle()`.**
   - In each of the three skip branches, call `withdraw(iid)` just before
     `return`:
     - the type branch (lines 216-218);
     - the listing-size branch (lines 219-221);
     - the post-download size branch inside the `try` (lines 228-230).
   - Don't add the return value to any count. This matches the
     unique-permissions branch on line 225.
   - Leave `seen.add(iid)` where it is (line 214). The full-listing sweep
     doesn't need it changed, because `handle` withdraws directly.
   - Verify: step 1's command. The SharePoint test passes.
   - Traps: `withdraw` runs on the autocommit `lock` connection, as it does
     everywhere else in this function. Don't open a new connection.

4. **`app/crawl.py:415-431`, `crawl_confluence()`, `do_page()` attachment loop.**
   - Call `withdraw(ext)` just before `continue` in each of these branches:
     - the type branch (lines 415-417);
     - the `fileSize` branch (lines 418-420);
     - the post-download size branch (lines 429-431).
   - Don't add any of them to `done`.
   - Don't add the return value to a count.
   - Verify: step 1's command. The Confluence test passes.
   - Traps:
     - `withdraw` takes `(ext, prefix=False)`. Use the default, exact match.
     - The skip checks must stay before `skip_unchanged` (line 424). An
       unchanged version number must not keep an oversized attachment live.

5. **Help text.**
   - **`app/templates/sources.html:22-25`**, the "Config (JSON)" field-help in
     the add-source form. After the Confluence line, add:
     `<br>Optional <code>"include_ext"</code> (default docx, pptx, pdf) limits the file types. Narrowing these withdraws matching documents already in search on the next crawl.`
     The spec placed this line "next to the file types and size settings".
     The page has no such fields: `include_ext` lives only in the config
     JSON, and the size caps are env vars. This help is the nearest place.
   - **`docs/user-guide/admins.md:41-42`.** Append to the "Skipped"
     sentence: `Files skipped for their type or size are also withdrawn if
     an earlier version is in search, so narrowing a source's types withdraws
     matching documents on the next crawl.`
     The spec doesn't list the guide, but the repo keeps it true, as #143
     did. Record this as a deviation if review objects.
   - Verify: run the full suite,
     `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`.
     The `test_sources.py` page tests still pass.
   - Traps: the page uses no JavaScript, so this is plain markup only.

## Tests

- The four tests from step 1 fail on main and pass on the branch.
- The full suite is green, including the empty-listing tests,
  `test_crawl_skips_type_and_size_without_downloading`,
  `test_type_and_size_filters` and `test_attachment_filters`.

## Known limit (not in the spec; for the approver)

**S3 can revive old text.** Suppose an S3 key is replaced by an oversized
version. It is skipped and withdrawn. Later the cap is raised by env, with the
file untouched.

- The key is known and older than the cursor, so it is not downloaded.
- The sweep clears `deleted_at`, and the old text is live again.

Adding skipped known keys to `retry` would force a download once they fit.
That is not planned here. If it is wanted, it is a one-line addition to
step 2.

## Rollback

Revert the commits. Documents withdrawn by this change come back on the next
crawl:

- **S3:** the sweep clears `deleted_at` for every listed key.
- **SharePoint and Confluence:** when delta or CQL next offers the item.

## Deviations
- Review: the help line and the guide sentence say `include_ext` applies to S3 and SharePoint. Confluence ignores it (its types are fixed in `crawl_confluence`), so the plan's wording would have promised a withdrawal that never happens there.
