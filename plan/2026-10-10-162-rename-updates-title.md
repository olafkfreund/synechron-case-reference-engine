---
status: approved
issue: 162
spec: spec/2026-10-10-162-rename-updates-title.md
---

# Plan: A same-bytes rename updates the document title

Approved decision: in `ingest()` (`app/ingest.py`), the same-checksum branch
also writes `title`. Nothing else changes on that path:

- It still returns `"skipped"`, so crawl counts are unchanged.
- No S3 put, no conversion, no triage, no extract job, and the case is not
  reopened. Only the document row changes.
- No crawler changes: S3 passes the key name, SharePoint the item name and
  Confluence the page or attachment title, each as `title`.
- The review list and page read `d.title`, so they show the new name on the
  next load. Search reads the case's extracted title and is unchanged.

Rejected: treating a rename as a new version (a conversion and model call, and
the case goes back to review, for a filename), and putting the title in the
checksum (every stored checksum changes, so the first crawl re-ingests
everything).

Coder handoff: no. Two steps, two files.

## Steps

1. **`tests/test_ingest.py`: add the test after `test_dedupe` (line 55).**
   `test_same_bytes_rename_updates_title(env)`:
   - `_, sid = env`
   - `assert ing.ingest(sid, "a", "old.docx", b"one") == "new"`, then
     `n = jobs()`.
   - `assert ing.ingest(sid, "a", "new.docx", b"one") == "skipped"`.
   - The document's `title` (select by `source_id=%s and external_id='a'`) is
     `"new.docx"`, and `jobs() == n`: the second call queued nothing.
   - Verify: `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_ingest.py -k rename`.
     It fails on main: the title stays `"old.docx"`.
   - Traps:
     - There is no bind mount, so always build before running tests.
     - Never run `docker compose up` or `down`.
     - Use made-up names only.
     - `jobs()` counts jobs for documents of `SID["v"]`, which the `env`
       fixture sets.

2. **`app/ingest.py:83-87`, `ingest()`, same-checksum branch.**
   - Change the update to:
     ```python
     conn.execute("update documents set acl_groups=(select acl_groups from sources where id=%s for share), "
                  "deleted_at=null, source_version=%s, title=%s where id=%s",
                  (source_id, source_version, title, row[0]))
     ```
   - Extend the comment to say "and a renamed file shows its new name (#162)".
   - Verify: step 1's command passes. Then run the full suite:
     `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`.
   - Traps:
     - Keep the `for share` subselect. It is how a concurrent admin save is
       never overwritten (#133).
     - Leave `skip_unchanged()` alone. It runs without a title (Confluence
       attachments with an unchanged version number), and the spec does not
       cover it.
     - #133 (PR #174) and #158 edit the lines just below this branch in
       `ingest()`. If one merges first, rebase. The conflict is textual only.

## Tests

- `pytest tests/test_ingest.py -k rename` fails on main and passes on the
  branch.
- The full suite is green. `test_dedupe` still expects `"skipped"` and one job.

## Rollback

Revert the commit. The title column keeps whatever was written, which is the
source's real current name, so nothing needs repairing.
