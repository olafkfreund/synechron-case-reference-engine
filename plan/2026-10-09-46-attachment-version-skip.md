---
status: approved
issue: 46
spec: spec/2026-10-09-46-attachment-version-skip.md
---

# Plan: Skip unchanged Confluence attachments before downloading them

Branch `fix/46-attachment-version-skip`, rebased on `origin/main` 8538b42.
All line numbers below are from that base.

## Approved decisions

Carried over from the spec; the intent and spec are not needed to implement this.

1. **Store the source version on the document.** Add a new nullable column
   `documents.source_version text`. It is additive and idempotent, and has
   no index: lookups use the existing unique `(source_id, external_id)`.
   Null means unknown, and an unknown item is downloaded.
2. **Version identity is `"<version.number>:<version.when>"`,** taken from
   the `expand=version` attachment listing the crawler already requests. If
   either value is missing, the version is `None` and the attachment is
   downloaded as today.
3. **Skip before the download.** If the stored version equals the current
   one, the crawler does not download the attachment. It has the same
   effect as today's checksum skip:
   - `deleted_at = null`;
   - `acl_groups` refreshed from the source under `for share`;
   - counted as `skipped`;
   - added to `done`.
4. **`ingest()` stores the version** on both the upsert and the
   checksum-skip path. Existing rows learn their version after one more
   download. A failed download or ingest never writes a version, so a
   retry downloads again.
5. **Safety order is unchanged.** `allowed()` runs for the page and its
   ancestors (`app/crawl.py` line 397) before any attachment is considered.
   Then come the type and size filters, then the version skip, then the
   download.
6. **Out of scope:** pages, SharePoint, S3, and the cursor slack
   (`CURSOR_SLACK`).

## Steps

1. **`sql/schema.sql`: add the column after the `documents_kind_check`
   block (after line 70, before the `cases.basis` comment on line 72).**

   ```sql
   -- the source's own version of a crawled item (Confluence: "<number>:<when>"), so an unchanged one
   -- is not downloaded again; null means unknown, and the item is downloaded as before
   alter table documents add column if not exists source_version text;
   ```

   - In `tests/test_db.py` `test_init_idempotent_and_tables` (line 11),
     add an assertion that the column exists:
     `select 1 from information_schema.columns where table_name='documents' and column_name='source_version'`.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_db.py -q`.
   All pass, and `init()` twice raises no error.

   Traps:
   - **The schema-repair pattern.** Never edit the `create table if not
     exists documents` block (lines 25-42): existing databases never re-run
     it. Use `alter table ... add column if not exists` as `cases.basis`
     (line 73), `research.*` (lines 100-104) and `sources.data_class`
     (line 120) do.
   - No `not null`, and no default. `app.db.init` (`app/db.py` line 58)
     re-runs the whole file on every start.
   - No bind mount: always build before pytest.
   - Run from the worktree. The compose project is `refs-engine-46`; never
     `up` or `down`.

2. **`app/ingest.py`: write the version, and add `skip_unchanged`.**
   - Line 62: the signature becomes
     `def ingest(source_id: int, external_id: str, title: str, data: bytes, source_version: str | None = None) -> str:`.
   - Lines 71-72 (checksum-skip `update`): add `source_version=%s`, so it
     sets `acl_groups=..., deleted_at=null, source_version=%s where id=%s`,
     and pass `source_version` in the params.
   - Lines 89-95 (upsert):
     - add `source_version` to the column list and to the
       `select %s,...` values;
     - in `do update set`, add `source_version=excluded.source_version`;
     - pass `source_version` in the params, in the right position.
   - Add below `ingest()`:

     ```python
     def skip_unchanged(source_id: int, external_id: str, source_version: str) -> bool:
         """The checksum skip without the download: same source version, so un-withdraw and refresh the groups."""
         with db.connect() as conn:
             return conn.execute(
                 "update documents set acl_groups=(select acl_groups from sources where id=%s for share), deleted_at=null "
                 "where source_id=%s and external_id=%s and source_version=%s returning id",
                 (source_id, source_id, external_id, source_version)).fetchone() is not None
     ```

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_ingest.py tests/test_crawl_confluence.py tests/test_crawl_sharepoint.py -q`.
   All pass. The other callers pass no version and write null.

   Traps:
   - Count the `%s` placeholders against the params in the upsert. It
     already has a nested `select ... for share`, and the order matters.
   - The groups must be read under `for share`, as everywhere else. That
     is the guard against a concurrent source save.

3. **`app/crawl.py`: skip before the download.**
   - Line 12: `from app.ingest import ingest, skip_unchanged`.
   - In `do_page()`, after the size filter (lines 412-414) and inside the
     `try:` on line 415, before
     `data = c.get(base + att["_links"]["download"]).content` (line 416),
     add:

     ```python
     v = att.get("version") or {}
     ver = f"{v['number']}:{v['when']}" if v.get("number") is not None and v.get("when") else None
     if ver and skip_unchanged(source_id, ext, ver):  # unchanged: no download (#46)
         counts["skipped"] += 1
         done.add(ext)
         continue
     ```

   - Line 420: `counts[ingest(source_id, ext, title, data, source_version=ver)] += 1`.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_crawl_confluence.py -q`.
   Existing tests pass unchanged: their fixtures have no `version.number`,
   so they download.

   Traps:
   - Call `ingest` by keyword (`source_version=ver`). The test wrapper
     `_change_groups_after_first_ingest` (`tests/test_crawl_confluence.py`
     line 287) passes `*a, **k` through, so it keeps working.
   - Keep the skip inside the existing `try`, so a DB error becomes a
     per-attachment `fail()` (fail closed), not a crashed crawl.

4. **`tests/test_crawl_confluence.py`: prove the skip, with made-up data.**
   - `Conf.attach()` (line 33) gets `number=None`. When it is set, the
     attachment's version is `{"when": when, "number": number}`.
   - Add these tests. "Download path" means
     `f"/wiki/download/attachments/{pid}/{title}"` in `cf.calls`.
     1. `test_unchanged_attachment_is_not_downloaded_again`:
        - Seed `p1` with `a1` (`number=1`), then crawl.
        - Move `p1`'s `when` forward so CQL offers the page again, clear
          `cf.calls`, and crawl again.
        - Assert there is no download path.
        - Assert `last_counts["skipped"] >= 1`.
        - Assert `att:p1:a1` is still live.
        - Assert `source_version` is `"1:2026-01-01T00:00:00.000Z"`.
     2. `test_changed_attachment_is_downloaded_and_version_stored`:
        - Second run with `number=2` and new bytes.
        - Assert one download, the new text stored, and `source_version`
          starting with `"2:"`.
     3. `test_unchanged_attachment_withdrawn_then_allowed_comes_back_without_download`:
        - Ingest, then restrict `p1` and crawl, so it is withdrawn.
        - Lift the restriction and move the page forward, then crawl.
        - Assert no download path, `att:p1:a1` live again, and its groups
          equal the source's.
        - Reuse the restriction helpers of
          `test_restriction_added_after_ingest_withdraws_page_and_attachments`
          (line 162).
     4. `test_null_version_row_downloads_once_then_skips`:
        - Ingest with `number=None`, which stores null.
        - Set `number=1` with the same bytes, and crawl: one download,
          counted `skipped` by checksum, version recorded.
        - Crawl again: no download.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_crawl_confluence.py -q`.
   All pass.

   Traps:
   - **Made-up data only:** fake ids, titles and bytes, as in the existing
     fixtures.
   - The fake CQL filter (line 62) compares `version.when`. To have a page
     offered again, move its `when` past the cursor minus slack, as
     `test_second_run_sends_lastmodified_and_fetches_only_changes`
     (line 137) does.

## Tests

- `docker compose build app && docker compose run --rm app pytest`: the
  whole suite is green.
- New:
  - the column assertion in `test_db`;
  - four Confluence tests: unchanged gives no download; changed gives a
    download and the new version; withdrawn and unchanged comes back
    without a download; a null version downloads once, then skips.

## Rollback

- `git revert` the implementation commits.
- The column stays. It is nullable and unused by old code, so it does no
  harm, and dropping it is optional:
  `alter table documents drop column if exists source_version;`.
- The first crawl after a re-deploy downloads every attachment once more,
  which is today's behaviour.

## Handoff

Steps 1-4 edit five files: `sql/schema.sql`, `tests/test_db.py`,
`app/ingest.py`, `app/crawl.py` and `tests/test_crawl_confluence.py`.
That is 3 or more steps that edit files, so per the model split this goes
to the `coder` agent:
- one `coder` started with this plan path and step 1;
- later steps sent with `SendMessage`;
- review by a fresh Opus agent given only this plan path and the diff.
