---
status: draft
issue: 206
intent: intent/2026-10-13-206-sharepoint-raised-cap.md
---

# Spec: Raising the size cap brings back the files it skipped

## Design

**Remember every file skipped as too large, with its size, in the crawl state
that already holds `retry_ids`. On the next run, fetch again only the ones whose
remembered size now fits under the cap.**

No separate "last cap" is stored. A remembered size at or under the current cap
can only mean the cap was raised. While the cap stays the same, every
remembered size is still over it, so a run makes no extra Graph or Confluence
calls. This meets the intent's last outcome without any comparison logic.

### SharePoint (`app/crawl.py`, `crawl_sharepoint`)

- New per-run dict `too_large: {item_id: size}`. Both size branches in
  `handle()` (`:225-228` metadata size, `:233-236` downloaded length) record
  `too_large[iid] = size` next to the existing count and withdraw.
- After the `retry_ids` loop (`:264-274`), a second loop over
  `last["too_large"]` does the following for each `iid, size`:
  - Skip it if `iid in seen`: delta already offered it this run, and `handle()`
    decided again.
  - If `size > cap`, carry it forward unchanged with no call
    (`too_large[iid] = size`).
  - Otherwise re-fetch it with `g.get(.../items/{iid})` and pass it to
    `handle()`, the same path the retry loop uses. That applies the type,
    permission and size checks and ingests the file, or records it again with
    its current size. A 404 means it was deleted: count it and drop it. Any
    other error appends it to `retry` and carries it forward.
- `handle()` marks deleted items as seen (`seen.add(iid)` before the deleted
  return), so a file deleted by delta drops out of `too_large`. Today deleted
  items never reach `seen`. Check `seen`'s other uses at `:277` and `:287` while
  planning; the full-listing withdrawal only reads ids, so the addition is safe.
- On a full resync (`full` is true), skip the carry-forward. The full listing
  rebuilds `too_large` from scratch, and anything not listed is gone.
- Store it: `last_counts` gets `"too_large": too_large` beside `"retry_ids"`
  (`:299-300`).

### Confluence (`crawl_confluence`), same gap, same fix

An attachment over `CONFLUENCE_MAX_BYTES` is only looked at again when CQL
offers its page or the attachment as modified. The fix keys on the page,
because `do_page(pid)` is how attachments are fetched (`:416-445`):

- The two size branches record `too_large[ext] = size`, where
  `ext = att:{pid}:{id}`.
- After the `retry_ids` loop, go through `last["too_large"]`. Entries whose
  page is already in `done` this run are skipped. Over-cap entries are carried
  forward. For the pages of the rest (each page once), `do_page(pid)` runs
  exactly as the retry loop does: a 404 withdraws, and any other error goes to
  `retry`.
- Saved as `"too_large"` in `last_counts`.

This settles the intent's open question: both crawlers are covered in one
change.

### Out of scope

S3 needs no change: it lists every key on every crawl, and #189 already
reconverts a withdrawn key.

## Alternatives rejected

- **Store the cap and do a full resync when it rises.** This re-walks and
  re-downloads the whole drive (thousands of calls) for a handful of files, and
  it throws away the cursor, which the intent forbids.
- **Put too-large files in `retry_ids`.** They would be re-fetched on every
  run, including while the cap has not changed, which breaks "no extra calls".
  It would also show up as failures.
- **A DB table of skipped files.** It needs a schema migration for state that
  fits in the JSON the source row already has.
- **Withdraw nothing and keep too-large files listed.** They have no text, so
  there is nothing to search. The issue is about bringing them back, not
  listing them.

## Risks

- **`last_counts` grows with the number of over-cap files.** These are rare at
  50 MB and stored as id plus size. They aren't capped: dropping entries would
  bring the bug back for those files. If a site has thousands of them, the
  admin page shows `last_counts`; check it doesn't dump the dict there (the
  plan greps for uses of `last_counts`).
- **Raising the cap causes one burst of downloads** of up to every remembered
  file. That is the intended outcome, and each file is fetched once.
- **A file shrinks without changing.** Not possible: shrinking is a change, so
  delta offers it again.
- **Permissions are unchanged and still fail closed.** A rechecked file goes
  through `handle()` and `do_page()`, the same code as a fresh one, including
  `same_access()` and `allowed()`.
- **State from before this change has no `too_large` key,** so the files skipped
  before it ships are not remembered. The intent's workaround (resetting the
  delta cursor) still covers those. The PR notes it.

## Verification

`tests/test_crawl_sharepoint.py`:
- **Raised cap returns an unchanged file:** skip `f1` at cap 15, raise the cap
  to 1000, then delta offers nothing. `f1` is fetched by id, goes live, and
  `too_large` is empty.
- **Same cap, no extra calls:** a second run at cap 15 with an empty delta
  makes no `items/f1` request, and `too_large` still holds `f1`.
- **Still too large after the raise:** at cap 18 with size 20, the file stays
  withdrawn and is remembered.
- **Deleted while skipped:** delta reports `f1` deleted, and `too_large` no
  longer holds it.
- **404 on recheck:** the file is dropped and stays withdrawn.

`tests/test_crawl_confluence.py`:
- **Raised cap returns an attachment** whose page and attachment are both
  unchanged.
- **Same cap, no extra page fetch.**

The full suite: `docker compose run --rm app pytest`.
