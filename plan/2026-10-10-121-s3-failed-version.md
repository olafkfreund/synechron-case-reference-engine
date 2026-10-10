---
status: draft
issue: 121
spec: spec/2026-10-10-121-s3-failed-version.md
---

# Plan: S3 crawl loses a new version of a known key once its ingest fails and the cursor moves on

These decisions are copied from the spec:

- `crawl_s3` stores an uncapped `retry_ids` list of every key that failed this
  run, in `last_counts`, the same as SharePoint and Confluence.
- On the next run, a key on that list is never skipped on the cursor.
- `failed_keys` keeps its display cap of 20.
- A key that now succeeds, or that has left the bucket, drops off the list.

## Steps

1. `app/crawl.py`:
   - **`:31-32`.** Select `last_counts` as well, as
     `config, cursor, last = lock.execute("select config, cursor, last_counts from sources where id=%s", (source_id,)).fetchone()`.
     Then add `prev_retry = set((last or {}).get("retry_ids", []))`.
   - **Next to `failed = []` (`:41`).** Add `retry = []`.
   - **`:58`.** `if key in known and since and modified < since and key not in prev_retry:`
   - **In the `except` (`:63`).** Add `retry.append(key)` before the capped
     `failed` append.
   - **`:89`.** Store `Jsonb({**counts, "failed_keys": failed, "retry_ids": retry})`.

   Verify with `pytest -q tests/test_ingest.py`: the existing tests pass.

   Traps:
   - The `else` branch (empty listing) also reaches the final update. `retry`
     is `[]` there, which is correct.
   - Don't store error messages. Keys only.
   - #120 doesn't touch this file. #127 and #129 touch other functions in it.

2. `tests/test_ingest.py`: after `test_bad_document_does_not_stop_crawl`, add
   `test_failed_new_version_is_retried_past_the_cursor(env, monkeypatch)`.
   1. Put `in/a.docx` with b"v1", then crawl.
   2. Monkeypatch `ing.to_markdown` to raise on b"v2". Put b"v2", then crawl.
      Assert `failed == 1`.
   3. `set_cursor(sid, "2999-01-01T00:00:00+00:00")`. Restore the plain
      decode, then crawl. Assert `updated == 1` and that the document's `text`
      is "v2".
   4. Crawl again. Assert `skipped == 0` and `updated == 0`, because the key
      is skipped on the cursor without a download.

   Verify that the test fails on main at step 3.

   Traps:
   - `env` already patches `to_markdown` to `data.decode()`. Wrap that, as
     `test_bad_document_does_not_stop_crawl` does.

## Tests

The full suite must pass, and the new test must fail on main.

## Rollback

Revert the commit. `retry_ids` in `last_counts` is ignored by the old code.
