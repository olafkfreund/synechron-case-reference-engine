---
status: approved
issue: 206
spec: spec/2026-10-13-206-sharepoint-raised-cap.md
---

# Plan: Raising the size cap brings back the files it skipped

Branch `fix/206-sharepoint-raised-cap`, worktree `../refs-engine-206`.

## Approved decisions

- Every file skipped as too large is remembered with its size, in
  `sources.last_counts["too_large"]` (`{id: size}`), next to `retry_ids`. There
  is no schema change and no stored "last cap".
- On the next run, a remembered file is fetched again only if its size is at or
  under the current cap. While the cap is unchanged, every size stays over it,
  so the run makes no extra calls.
- A fetched-again file goes through the same handler as a fresh one
  (SharePoint `handle()`, Confluence `do_page()`), so the type, permission and
  size checks still fail closed.
- Deleted files drop out of the list: a delta delete or a 404 on SharePoint, and
  a page 404 or an attachment no longer listed on Confluence. Any other error
  goes to `retry`, and the retry loop takes it from there.
- On a SharePoint full resync, nothing is carried forward. The full listing
  rebuilds the list.
- Both SharePoint and Confluence get the fix. S3 is unchanged.
- Files skipped before this ships are not remembered. Resetting the cursor
  once still covers them, and the PR says so.

## Steps

1. **`app/crawl.py`, `crawl_sharepoint`: remember and re-fetch.**
   - `:195`: `failed, retry, seen, checked, too_large = [], [], set(), set(), {}`.
   - `handle()`, deleted branch (`:214-216`): `seen.add(iid)` before the
     withdraw, so a deleted file leaves `too_large`. Leave the comment as is.
   - Metadata size branch (`:225-228`): add `too_large[iid] = item.get("size", 0)`.
   - Downloaded size branch (`:233-236`): add `too_large[iid] = len(data)`.
   - After the `retry_ids` loop (`:264-274`), add the following, with the same
     error handling as the retry loop:
     ```python
     # last run's too-large files: delta will not offer them again; fetch those the cap now admits (#206)
     for iid, size in ({} if full else (last or {}).get("too_large", {})).items():
         if iid in seen:
             continue
         if size > cap:
             too_large[iid] = size
             continue
         try:
             handle(g.get(f"{GRAPH}/drives/{drive}/items/{iid}").json())
         except Exception as e:  # noqa: BLE001 - one bad item must not stop the crawl (#129)
             if getattr(e, "status", None) == 404:
                 counts["deleted"] += withdraw(iid)
             else:
                 retry.append(iid)
     ```
   - `:300`: add `"too_large": too_large` to the stored JSON.
   - Docstring (`:174-183`): add one sentence: "Files skipped as too large
     are remembered with their size and fetched again once the cap admits them
     (#206)."
   → verify: `grep -n "too_large" app/crawl.py` shows the record, the loop and
   the store for SharePoint.
   Traps:
   - `seen` also feeds the full-resync withdrawal (`:277`). Deleted ids in
     `seen` are already withdrawn, so that is safe.
   - Don't move the loop after the live re-check (`:282`). It has to run
     before it, so a re-ingested file is in `checked`.

2. **`tests/test_crawl_sharepoint.py`: SharePoint tests.** Add these after
   `test_sharepoint_withdrawn_file_returns_with_new_text` (`:119`). Each uses
   `first_run`, `f(iid, size=...)` and `page(...)`, and sets
   `SHAREPOINT_MAX_BYTES` with `monkeypatch`.
   - `test_raised_cap_returns_an_unchanged_file`:
     - At cap 15, delta d1 offers `f1` at size 20. It is withdrawn, and
       `source(sid)[1]["too_large"] == {"f1": 20}`.
     - Then raise the cap to 1000 and have delta d2 offer an empty page
       (`page([], delta=…d3)`). Expect `f1` live, `"updated"` or `"skipped"`
       counted, and `too_large == {}`.
     - The fake's item metadata returns size 10, so `handle()` passes the size
       check, and `sp.files["f1"]` gives the bytes.
   - `test_same_cap_makes_no_extra_call`: as above but keep cap 15 for the
       second run. Clear `sp.calls` first, then assert no call path contains
       `/items/f1` and `too_large == {"f1": 20}`.
   - `test_still_too_large_after_a_small_raise`: raise to cap 18 (still under
     20). `f1` stays withdrawn, `too_large == {"f1": 20}`, and no `/items/f1`
     call.
   - `test_deleted_while_skipped_is_forgotten`: after the skip, delta d2 offers
     `{"id": "f1", "deleted": {}}`. Then `too_large == {}`.
   - `test_404_on_refetch_is_forgotten`: after the skip, `del sp.files["f1"]`
     (the fake's metadata returns 404), raise the cap, and send an empty delta.
     `f1` stays withdrawn and `too_large == {}`.
   → verify: `docker compose run --rm app pytest tests/test_crawl_sharepoint.py`
   passes.
   Traps: the fake keys delta pages by full URL with `?token=`. Chain d1 → d2
   → d3 as the existing cap test does.

3. **`app/crawl.py`, `crawl_confluence`: remember and re-crawl the page.**
   - `:374`: add `too_large = {}` to the per-run state, as
     `failed, retry, done, cache, visited, too_large = ..., {}`.
   - Metadata size branch (`:427-430`): add
     `too_large[ext] = att.get("extensions", {}).get("fileSize", 0)`.
   - Downloaded size branch (`:438-441`): add `too_large[ext] = len(data)`.
   - After the `retry_ids` loop (`:478-487`):
     ```python
     # last run's too-large attachments: CQL will not offer them again; re-crawl pages the cap now admits (#206)
     again = []
     for ext, size in (last or {}).get("too_large", {}).items():
         pid = ext.split(":")[1]
         if pid in visited:
             continue
         if size > cap:
             too_large[ext] = size
         elif pid not in again:
             again.append(pid)
     for pid in again:
         try:
             do_page(c.get(f"{c.api}/content/{pid}?expand=body.storage,version,ancestors,space").json())
         except Exception as e:  # noqa: BLE001 (#129)
             if getattr(e, "status", None) == 404:
                 withdraw(f"page:{pid}")
                 withdraw(f"att:{pid}:", prefix=True)
             else:
                 retry.append(pid)
     ```
   - `:521`: add `"too_large": too_large` to the stored JSON.
   - Docstring (`:350-359`): add the same sentence as in step 1, worded for
     attachments.
   → verify: `grep -n "too_large\b" app/crawl.py` shows the record, the loop and
   the store for Confluence.
   Traps:
   - `do_page()` adds `pid` to `visited` (`:409`), so a page visited earlier
     this run is skipped, and its attachments are recorded again if they are
     still too large.
   - If one page holds an attachment that fits and another that is still over
     the cap, the page is re-crawled once, and `do_page()` records the second
     attachment again. That is right, even though the carry loop doesn't
     record it in that case.
   - A restricted page returns early in `do_page()`, so its attachments drop
     out of `too_large` and are withdrawn. That is right.

4. **`tests/test_crawl_confluence.py`: Confluence tests.** Add these after
   `test_confluence_withdrawn_attachment_returns_with_new_text` (`:241`).
   - `test_confluence_raised_cap_returns_an_unchanged_attachment`:
     - Use cap 15. Page `p1` has attachment `a1` of size 20 at
       `when="2026-01-01…"`. Crawl: it is withdrawn, and
       `src(sid)[1]["too_large"] == {"att:p1:a1": 20}`.
     - Then add a second page `p2` with `when="2026-03-01…"` and crawl once, so
       the cursor moves past `p1`.
     - Raise the cap to 1000, `cf.calls.clear()`, and crawl. Expect `att:p1:a1`
       live, the `DL` download in `cf.calls`, and `too_large == {}`.
   - `test_confluence_same_cap_makes_no_extra_page_fetch`: the same setup, but
     keep the cap at 15 on the last crawl. Expect no call to
     `.../content/p1` with body expand and no `DL`, and
     `too_large == {"att:p1:a1": 20}`. Use `cf.calls`, which holds request
     paths. The live re-check fetches `/content/p1` for `page:p1`, so assert
     `DL not in cf.calls` and that `too_large` is unchanged, rather than
     counting page fetches.
   → verify: `docker compose run --rm app pytest tests/test_crawl_confluence.py`
   passes.
   Traps:
   - `CURSOR_SLACK` is one day (`crawl.py:16`): CQL re-offers anything modified
     within a day before the cursor. Without the later page `p2`, the fake
     re-offers `p1` on every run, and the test proves nothing.
   - Check that the new test fails with step 3's loop removed.

5. **`app/templates/sources.html:66`: hide the list.** Add `"too_large"` to the
   excluded keys: `if k not in ("failed_keys", "retry_ids", "too_large")`.
   **`README.md:24-25`**: after "counted as `skipped_too_large` on the sources
   page.", add: "Raising the cap brings back, on the next crawl, files skipped
   as too large since this was added (#206)."
   → verify: `grep -n too_large app/templates/sources.html` shows the
   exclusion.
   Traps: the sources page must not print item ids, which is what
   `failed_keys` and `retry_ids` are hidden for.

## Tests

```bash
docker compose run --rm app pytest tests/test_crawl_sharepoint.py tests/test_crawl_confluence.py
docker compose run --rm app pytest
```

Both should pass, with no AWS or Bedrock involved (fake Graph and Confluence).

## Rollback

Revert the branch's commits. A stored `too_large` key in `last_counts` is
ignored by the old code, so no data cleanup is needed.
