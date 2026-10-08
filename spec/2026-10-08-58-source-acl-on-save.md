---
status: draft
issue: 58
intent: intent/2026-10-08-58-source-acl-on-save.md
---

# Spec: Source access changes apply to documents on save

## Design

`documents.acl_groups` stays the column every reader filters on (`ACL` in
`app/review.py` line 23, used by review, search, render and research). It
remains a copy of `sources.acl_groups`. What changes is **when and from where
the copy is written**:

1. **On save.** `update()` in `app/sources.py` already locks the source row
   (`select … for update`). It now also reads the old `acl_groups`, and when
   the groups change it runs, in the same transaction:
   - `update documents set acl_groups = %s where source_id = %s`;
   - an insert into a new append-only table `source_acl_changes`
     (intent question 1).

   When the save returns, every document has the new groups.
2. **Every other write reads the source's current groups, under a row lock,
   in the same statement.** No write uses a copy taken earlier.
   - `ingest()` (`app/ingest.py`), in its insert/upsert and in its
     unchanged-checksum branch: the value comes from
     `(select acl_groups from sources where id = %s for share)`. The
     `acl_groups` parameter is removed, and its four call sites in
     `app/crawl.py` (lines 62, 226, 399, 416) stop passing it.
   - The end-of-crawl restamps in `app/crawl.py` (S3 line 76, SharePoint
     line 282, Confluence line 484) become
     `update documents d set acl_groups = s.acl_groups from (select acl_groups from sources where id = %s for share) s where d.source_id = %s …`.
     The S3 statement keeps its `deleted_at` logic.
   - The crawlers still read `acl_groups` at the start only to check the
     source exists. That read decides nothing about access.

   **Why the row lock.** A save holds `FOR UPDATE` on the source row until it
   commits. A write that needs `FOR SHARE` on the same row waits for that
   commit, and under READ COMMITTED it then reads the new groups. A write
   that got there first holds `FOR SHARE`, so the save waits for it and then
   updates every row it wrote. Either way, no document can end up with
   groups the admin has since replaced. This also covers SharePoint and
   Confluence, whose restamp runs on an autocommit connection: it is one
   statement, so the lock and the write happen together.
3. **The audit log.** New table `source_acl_changes`: `id`, `source_id`,
   `old_groups text[]`, `new_groups text[] not null`, `changed_by`,
   `changed_at`. It is append-only like `source_class_changes`: `sql/roles.sql`
   revokes update and delete from `refs_app`. The audit page (`app/audit.py`)
   lists the latest 100 next to the class changes.

Out of scope (intent):

- "enabled" stays a crawl switch only.
- Outputs already downloaded can't be taken back.
- Per-document permissions stay as today: items with their own permissions
  are skipped and withdrawn.

## Alternatives rejected

- **Filter on the source at read time** (`ACL` joins `sources` and drops
  `documents.acl_groups`). It gives a single source of truth with no copy,
  but it reverses the #1 design ("ACL lives on documents only"). That design
  keeps room for per-document access later, and its GIN index serves search.
  It would also rewrite the setup of every reader test. The copy is fine
  once every write of it is serialised against the save.
- **Only update documents on save**, leaving the crawlers alone. That fixes
  the reported lag but not the second bug in the intent: a crawl already
  running would write the old groups back.
- **Re-read the groups in Python before each write.** The value can still
  change between the read and the write. Only the lock in the same statement
  closes that window.
- **Serialise on the crawl's advisory lock.** A save would wait for an
  hours-long crawl, and an admin removing access must not wait.

## Risks

- **Save time:** one indexed update over a source's documents (thousands)
  inside the save. Expected well under a second.
- **Lock waits:** a save waits for the in-flight ingest statement (one row)
  or restamp (one statement) at most, never for the whole crawl, because
  each `FOR SHARE` lasts only its own transaction or statement. An
  `ingest()` transaction covers its select and insert only: Docling and the
  LLM run outside it (`app/ingest.py` structure today). The plan must check
  that this stays true.
- **Test churn:** tests that call `ingest(…, acl_groups)` or assert a
  document's groups from that argument must set the source's groups
  instead (`tests/test_ingest.py`, `test_crawl_*`, `test_review.py`,
  `test_sources.py`, `test_models_admin.py`, `test_research_claims.py`).
- **Hosts:** app and worker on ECS. The schema change is additive (a new
  table) and idempotent. Rollback is a revert; the new table can stay.

## Verification

- `docker compose build app && docker compose run --rm app pytest` is green.
- New tests:
  - saving a source with fewer groups changes every document's groups before
    the response returns, and a user in the removed group loses access on
    review and search at once;
  - **a crawl in progress cannot revert a save.** During an S3 crawl (an
    `ingest` wrapper calls the save between two files), the groups are
    changed. After the crawl, every document, including those ingested before
    and after the save, has the new groups;
  - the same for the SharePoint and Confluence restamps, with their mocked
    APIs;
  - **lock ordering:** with a save transaction held open in one connection,
    an `ingest()` in another thread blocks, and once the save commits it
    writes the new groups;
  - a change writes exactly one `source_acl_changes` row with the old and
    new groups, and saving the same groups writes none; `refs_app` cannot
    update or delete it (as for `source_class_changes`);
  - the audit page lists the change.
