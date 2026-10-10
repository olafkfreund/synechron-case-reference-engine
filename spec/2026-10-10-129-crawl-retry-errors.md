---
status: draft
issue: 129
intent: intent/2026-10-10-129-crawl-retry-errors.md
---

# Spec: A non-HTTP error on one retried item fails the whole SharePoint or Confluence crawl, every run

## Design

The approved intent has no open questions. Three per-item `except` clauses
widen from the crawler's error class to `Exception`, and each keeps its 404
branch. The `handle()` and `do_page()` calls inside them already catch their
own errors (`app/crawl.py:229`, `:430`), so only the item fetch can escape.

1. **SharePoint `retry_ids` loop**, `:255-262`:
   ```python
   except Exception as e:  # noqa: BLE001 - one bad item must not stop the crawl (#129)
       if getattr(e, "status", None) == 404:
           counts["deleted"] += withdraw(iid)
       else:
           retry.append(iid)
   ```
2. **Confluence `retry_ids` loop**, `:468-474`: the same change, keeping its
   two withdraws on 404.
3. **Confluence fetch of an attachment's page**, `:456-459`:
   `except ConfluenceError as e:` becomes `except Exception as e:  # noqa: BLE001`,
   and it still calls `fail(pid, f"page:{pid}", e)`. `fail()` withdraws the
   page, records only the type name, and puts the page on `retry_ids`.

Unchanged:

- **Enumeration errors.** Delta paging (`:240-250`) and CQL paging (`:451`)
  still raise and keep the cursor.
- **Messages.** None are stored. `failed_keys` holds the type name only.
- **Counts.** A retried item that fails again is not counted in `failed`, the
  same as a non-404 `GraphError` today. It stays on `retry_ids`.

## Alternatives rejected

- **Wrap transport errors in `GraphError`/`ConfluenceError` inside `get()`.**
  That also changes the enumeration path, which has to keep failing the crawl
  and keeping the cursor. It is a larger change.
- **Drop the item from `retry_ids` after N failures.** The item would be lost
  silently, and the intent doesn't ask for it.

## Risks

- **A programming error, like a `KeyError` on an odd API reply, is now
  retried instead of failing loudly.** That matches every other per-item path
  in the crawlers. The item stays on `retry_ids`, and the source's last counts
  show it.
- **Rebase overlap with #127.** #127 edits the `expand` on the same lines
  (`:457`, `:469`). Whichever merges second rebases with a small conflict.
- **No schema change, no host impact.**

## Verification

New tests. Each wraps the fake's `MockTransport` handler so that one path
raises `httpx.ReadTimeout`:

- `tests/test_crawl_sharepoint.py`,
  `test_timeout_on_retried_item_does_not_stop_the_crawl`: an item fails, so it
  is on `retry_ids`. Next run, fetching it times out, while another new item
  is present. The crawl returns counts and the other item is ingested. The
  item stays on `retry_ids`, and the cursor advances. On main the crawl
  raises.
- `tests/test_crawl_confluence.py`, two tests:
  - the same case for a page on `retry_ids`;
  - `test_timeout_on_attachment_page_fetch_is_a_page_failure`: an attachment
    hit whose page fetch times out gives `failed == 1`, puts the page on
    `retry_ids`, and the crawl finishes. On main it raises.
- The existing 404 tests still pass:
  - `test_failure_withdraws_and_is_retried_next_run`;
  - `test_failed_item_is_retried_next_run`.

The full suite passes.
