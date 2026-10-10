---
status: draft
issue: 121
intent: intent/2026-10-10-121-s3-failed-version.md
---

# Spec: S3 crawl loses a new version of a known key once its ingest fails and the cursor moves on

## Design

The approved answer: no cap on the stored retry list, the same as SharePoint
and Confluence.

`crawl_s3` (`app/crawl.py:17-90`) follows the SharePoint and Confluence
pattern (`retry_ids`, `:255`, `:289`, `:465`, `:498`):

1. **Read last run's failures.** `:31` selects `last_counts` as well:
   ```python
   config, cursor, last = lock.execute("select config, cursor, last_counts from sources where id=%s", ...)
   prev_retry = set((last or {}).get("retry_ids", []))
   ```
2. **Never skip them on the cursor.** `:58` becomes
   ```python
   if key in known and since and modified < since and key not in prev_retry:
   ```
3. **Record every failure.** A new list, `retry = []`, sits beside `failed`.
   The `except` at `:63` appends `key` with no cap; `failed` keeps its display
   cap (`MAX_FAILED_KEYS`). A retried key that now succeeds is not appended,
   so it drops off the list.
4. **Store the list.** `:89` stores `{**counts, "failed_keys": failed, "retry_ids": retry}`.

Two consequences follow:

- **A key that left the bucket drops off.** It is not in the listing, so it
  is never attempted or appended. The deletion pass then withdraws it.
- **A key still failing stays on.** It is retried on every crawl, and its
  failure is counted each time.
- **A failed new key needed no change.** It is never known, so it was already
  retried. Being on the list is harmless.

## Alternatives rejected

- **Hold the cursor back to the oldest failed key.** One permanently broken
  file would pin the cursor, so every crawl would re-download everything after
  it.
- **Read back `failed_keys`.** It is capped at 20 for display, so failures
  past 20 would be lost. The answer and the constraint require no cap.
- **A new table of failed keys.** That needs a schema change for a list the
  other crawlers keep in `last_counts`.

## Risks

- **A permanently broken file is downloaded every crawl.** That is the same
  as SharePoint and Confluence, and the source page shows the failure count.
- **Size of `last_counts`.** It grows by one key per failing file, the same as
  the other crawlers.
- **Upload sources share this crawler, so they get the fix too.**
- **No host impact, no schema change.**

## Verification

A new test in `tests/test_ingest.py`, using moto, `set_cursor` and the
`to_markdown` monkeypatch from `test_bad_document_does_not_stop_crawl`:

1. Ingest v1 of `in/a.docx`.
2. Upload v2 and make it fail: the crawl reports `failed == 1`.
3. Move the cursor far past it, then make conversion succeed. The next crawl
   reports `updated == 1`, and the document text is v2. It is 0 and v1 on
   main.
4. A further crawl skips it, because it is off the retry list.

The full suite passes.
