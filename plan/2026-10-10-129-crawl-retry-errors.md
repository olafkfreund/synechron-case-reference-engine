---
status: draft
issue: 129
spec: spec/2026-10-10-129-crawl-retry-errors.md
---

# Plan: A non-HTTP error on one retried item fails the whole SharePoint or Confluence crawl, every run

These decisions are copied from the approved spec:

- **Widen three per-item `except` clauses to `Exception`.** These are:
  - the SharePoint `retry_ids` loop;
  - the Confluence `retry_ids` loop;
  - the Confluence fetch of an attachment's page.
- **Keep the 404 branches.** On a 404, withdraw (SharePoint), or withdraw the
  page and its attachments (Confluence). Anything else goes back on
  `retry_ids`, and the attachment-page path calls `fail()`.
- **Enumeration errors still fail the crawl** and keep the cursor. This
  covers delta paging and CQL paging.
- **No error messages are stored.** `failed_keys` keeps the type name only,
  as it does today.

**Size:** 2 file-editing steps. Step 2 edits 2 test files, so the plan
touches 3 files in total: `app/crawl.py`, `tests/test_crawl_sharepoint.py` and
`tests/test_crawl_confluence.py`. That meets the coder threshold of 3 or more
files.

**Overlap with #127:** #127 edits the `expand` string on `:457` and `:469`.
This plan edits the `except` lines right after them, `:458` and `:470`.
Whichever merges second rebases and keeps both changes.

## Steps

1. `app/crawl.py`:
   - **SharePoint `:259`:** `except GraphError as e:` becomes
     `except Exception as e:  # noqa: BLE001 - one bad item must not stop the crawl (#129)`.
     `:260` becomes `if getattr(e, "status", None) == 404:`.
   - **Confluence `:458`:** `except ConfluenceError as e:` becomes
     `except Exception as e:  # noqa: BLE001 (#129)`. The body
     `fail(pid, f"page:{pid}", e)` is unchanged.
   - **Confluence `:470`:** the same widening. `:471` becomes
     `if getattr(e, "status", None) == 404:`.

   Verify:
   - `grep -n "except GraphError\|except ConfluenceError" app/crawl.py`
     shows only the clauses outside these three paths, if any.
   - `pytest -q tests/test_crawl_sharepoint.py tests/test_crawl_confluence.py`
     passes.

   Traps:
   - `GraphError` and `ConfluenceError` both carry `.status`. Plain
     exceptions don't, so use `getattr(e, "status", None)` and not
     `e.status`.
   - Don't widen the delta loop at `:240-250` or the CQL paging at `:451`.
     They must keep raising.
   - `fail()` records `type(e).__name__` only, so a timeout's message, which
     can contain URLs, is never stored.

2. Tests:
   - **`tests/test_crawl_sharepoint.py`**, after
     `test_failed_item_is_retried_next_run` (`:276`):
     `test_timeout_on_retried_item_does_not_stop_the_crawl`.
     1. Make `f1` fail once the way that test does, so `retry_ids == ["f1"]`.
     2. Next run, wrap the `Site` handler so a GET of `.../items/f1` raises
        `httpx.ReadTimeout("t")`, with a new item `f2` in the delta.
     3. The crawl returns counts and does not raise.
     4. `f2` is ingested, `retry_ids == ["f1"]`, and the delta link is
        stored.
   - **`tests/test_crawl_confluence.py`:**
     - `test_timeout_on_retried_page_does_not_stop_the_crawl`. Make `p1`
       fail once with the restriction 500 queue, as in
       `test_failure_withdraws_and_is_retried_next_run` (`:293`). Then make
       `GET {API}/p1` raise `httpx.ReadTimeout`. The crawl finishes, and
       `retry_ids == ["p1"]`.
     - `test_timeout_on_attachment_page_fetch_is_a_page_failure`. Set up an
       attachment hit on `p1`, and make the by-id `GET {API}/p1` raise.
       Then:
       - `failed == 1`;
       - `failed_keys[0]["error"] == "ReadTimeout"`;
       - `"p1" in retry_ids`;
       - the crawl finishes.

   Verify:
   - Each new test fails on main with `httpx.ReadTimeout`. Stash
     `app/crawl.py`, rebuild, run, then pop and rebuild.
   - The existing 404 tests still pass.

   Traps:
   - Raise from inside the `MockTransport` handler. Wrap
     `crawl.TRANSPORT = httpx.MockTransport(lambda r: boom(r) or site(r))`,
     or add a raise-set to the fake. httpx surfaces a handler exception
     unchanged.
   - Before raising, check the path exactly. The attachment-page path and
     the retry path both GET `{API}/p1`. The page-hit search must not be
     affected.
   - Don't trip the client's own retry and sleep: the fixtures already
     patch `time.sleep`.

## Tests

The full suite passes, and the three new tests fail on main.

## Rollback

Revert the commits.
