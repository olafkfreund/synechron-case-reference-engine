---
status: approved
issue: 46
intent: intent/2026-10-09-46-attachment-version-skip.md
---

# Spec: Skip unchanged Confluence attachments before downloading them

## Design

Store the Confluence version of each ingested attachment. On the next
crawl, compare it before downloading. An unchanged attachment is handled
exactly like today's checksum skip, without fetching the bytes.

### Schema: `sql/schema.sql`

Add an additive, idempotent repair after the `documents_kind_check` block
(lines 67-70). This follows the `alter table ... add column if not exists`
pattern used for `cases.basis` (line 73), the `research` columns
(lines 100-104) and `sources.data_class` (line 120):

```sql
-- the source's own version of a crawled item (Confluence: "<number>:<when>"), so an unchanged one
-- is not downloaded again; null means unknown, and the item is downloaded as before
alter table documents add column if not exists source_version text;
```

- The column is nullable with no default. Existing rows get null, so their
  attachments are downloaded once more and their version is recorded.
- No index: lookups go by `(source_id, external_id)`, which the existing
  unique constraint (line 41) already covers.

### Ingest: `app/ingest.py`

- `ingest()` (line 62) gets `source_version: str | None = None`.
  - The checksum-skip `update` (lines 71-72) also sets
    `source_version=%s`. Existing rows with the same bytes record their
    version on the first run after deploy.
  - The upsert (lines 89-95) writes `source_version`, with
    `source_version=excluded.source_version` in `do update`.
  - Every other caller (S3, SharePoint, upload) passes nothing and writes
    null, as today.
- New function `skip_unchanged(source_id, external_id, source_version) ->
  bool`. It is one statement and has the same effect as the checksum skip:

  ```sql
  update documents set acl_groups=(select acl_groups from sources where id=%s for share), deleted_at=null
  where source_id=%s and external_id=%s and source_version=%s returning id
  ```

  It returns True when a row matched. A null `source_version` never matches
  in SQL, so unknown always means download.

### Crawl: `app/crawl.py` `do_page()` (lines 405-423)

The attachment list already uses `expand=version` (line 405). After the
type filter (lines 409-411) and the size filter (lines 412-414), and before
the download (line 416):

```python
v = att.get("version") or {}
ver = f"{v['number']}:{v['when']}" if v.get("number") is not None and v.get("when") else None
if ver and skip_unchanged(source_id, ext, ver):
    counts["skipped"] += 1
    done.add(ext)
    continue
```

Then `ingest(source_id, ext, title, data, source_version=ver)` on line 420.

The check runs inside the existing `try` (line 415), so a DB error is still
a per-attachment `fail()`. The order of checks keeps the existing
guarantees:

- `allowed(pid, page)` (line 397) has already passed for the page and every
  ancestor before any attachment is looked at. A skip can't make a
  restricted item searchable.
- `done.add(ext)` keeps the re-check pass (lines 471-486) as it is today
  for items handled in this run.
- A withdrawn but unchanged attachment comes back un-withdrawn and gets the
  source's current groups. That covers a page that was restricted and then
  allowed again, or an attachment that failed after its row was written.
  This is the same as the checksum path.
- A failed download or ingest never writes a version (the upsert is the
  last step). So the item retried by page id (`retry_ids`, lines 459-469)
  is downloaded again.

### Version identity (approved Q2)

The identity is `"<version.number>:<version.when>"`, both from the
`expand=version` response we already get, on Cloud and Data Center v1.
Every new upload creates a new number. Including `when` also covers a
re-upload that resets the number. If either value is missing, the
attachment is downloaded (fail open towards freshness, which the intent
requires).

### Not changed

- Pages (approved Q3).
- SharePoint and S3 crawls.
- The cursor slack (`CURSOR_SLACK`, line 16): it stays for safety, and it
  is now cheap for attachments.

## Alternatives rejected

- **Infer "unchanged" from the cursor** (`version.when` at or before the
  last cursor). No schema change, but wrong for items retried by page id
  after a failure. Their timestamps are old and they still need the
  download.
- **A map in `sources.last_counts`.** The JSON grows with every attachment,
  is rewritten every run, and a failed run could lose it.
- **A separate `source_versions` table.** More schema and a join for the
  same one-to-one fact.
- **Skip in `ingest()` by passing the version without the bytes.** This
  would mix "fetch" and "store" in one call and need a lazy-download
  callback. A separate `skip_unchanged()` is smaller and keeps `ingest()`
  as it is for the other callers.
- **HTTP conditional download (ETag / If-None-Match).** Confluence
  download URLs make no guarantee to support it, and we'd still need to
  store the ETag.
- **Version number alone.** It costs the same as number plus `when` and is
  a little less safe.

## Risks

- **A wrong skip hides a real change.** This could only happen if
  Confluence served new bytes under the same version number and timestamp.
  Confluence doesn't do that: every upload creates a new version. A
  missing version falls back to a download.
- **Migration.** `add column if not exists` is idempotent and applied by
  `app.db` init (`app/db.py` line 58, "Idempotent"). Old code ignores the
  column, so a rollback needs no schema change.
- **First run after deploy.** Every existing attachment downloads once more,
  because its stored version is null. That run is as costly as today's,
  and the next one isn't.
- **ACL.** The skip refreshes the groups under `for share`, the same way as
  the checksum path. The mid-crawl group-change guard (test
  `test_crawl_does_not_write_back_groups_changed_mid_crawl`) still applies.
- **Hosts.** The ECS worker (`crawl_confluence` jobs) and local compose.
  RDS gets one new nullable column.

## Verification

Run `docker compose build app && docker compose run --rm app pytest`. The
new tests go in `tests/test_crawl_confluence.py`, with made-up data:
`attach()` gains an optional `number` in its `version`.

- **Unchanged attachment, second crawl.** The page's `when` is moved
  forward so the page is offered again. The attachment keeps its number and
  `when`. Expected: no request to its `/download/...` path in `cf.calls`,
  and it is counted as `skipped`. The document stays live and its
  `source_version` is unchanged.
- **Changed attachment.** The number is bumped and the data is new.
  Expected: one download, the document's text is updated, and the new
  version is stored.
- **Withdrawn while restricted, then allowed again, version unchanged.**
  Expected: no download, `deleted_at` is null again, and the groups are the
  source's.
- **No version number from the API** (the existing fixtures). Expected:
  downloaded as today. All existing tests stay green unchanged.
- **Existing row with a null version and the same bytes.** Expected: one
  download, counted as `skipped` by checksum, and its version recorded. The
  next run doesn't download it.
- **Schema.** `db.init()` run twice raises no error, and the column exists
  (covered by `tests/test_db.py`-style idempotency, if it is not already
  generic).
