---
status: approved
issue: 58
spec: spec/2026-10-08-58-source-acl-on-save.md
---

# Plan: Source access changes apply to documents on save

## Approved decisions (self-contained)

- `documents.acl_groups` stays the column every reader filters on (`ACL`,
  `app/review.py` line 23). It is still a copy of `sources.acl_groups`.
- **Save:** `update()` in `app/sources.py` locks the source row `for update`
  (as today) and reads its old groups. When the groups change, the same
  transaction runs `update documents set acl_groups = <new> where
  source_id = <sid>` and writes one row to a new append-only table
  `source_acl_changes`.
- **Every other write of a document's groups** reads them from the source in
  the same statement, under `for share`:
  `(select acl_groups from sources where id = %s for share)`. No write uses a
  copy read earlier. `ingest()` loses its `acl_groups` parameter, and the
  crawlers stop reading `acl_groups` at the start.
- **Why the lock:** a save's `for update` makes a `for share` write wait,
  after which it reads the new groups (READ COMMITTED). A write that got
  there first makes the save wait, and the save then overwrites its rows. A
  save never waits for a whole crawl.
- **Log:** `source_acl_changes(id, source_id, old_groups text[], new_groups
  text[] not null, changed_by, changed_at)`. `refs_app` cannot update or
  delete it. The audit page lists the latest 100.
- Out of scope: "enabled" hiding documents; outputs already downloaded;
  per-document permissions (still skipped and withdrawn).
- Implemented by the `coder` agent (5 steps, 8+ files), reviewed by a fresh
  Opus agent.

## Steps

1. **Log table.**
   - `sql/schema.sql`, after `source_class_changes` (line 140–147): `create
     table if not exists source_acl_changes (…)` as above, with a one-line
     comment that it is append-only.
   - `sql/roles.sql` line 19: add `revoke update, delete on
     source_acl_changes from refs_app;`.

   → verify by `pytest tests/test_db.py`, extended: the table exists, and as
   `refs_app` an update or delete raises (copy the existing
   `source_class_changes` test).
   Traps: compose has no bind mount, so run `docker compose build app` before
   every test run in every step.
   *Done, with deviation:* no `source_class_changes` privilege test existed to
   copy. The new test lives in `tests/test_hardening.py`, where the
   `refs_app` fixture is, and covers both log tables.
2. **Save applies at once.** `app/sources.py`:
   - line 77: select `data_class, acl_groups … for update`;
   - after the `update sources` statement (line 80): if
     `groups(acl_groups) != old[1]`, run `update documents set acl_groups=%s
     where source_id=%s` and `log_acl_change(conn, sid, old[1], new,
     user.sub)`, a helper beside `log_class_change` (line 22).

   → verify by `pytest tests/test_sources.py tests/test_models_admin.py`,
   extended:
   - after a save that removes group G, every document of the source lacks G
     before the next request. A user only in G gets 404 on `/review/<id>` and
     no hit in search;
   - one `source_acl_changes` row with the old and new groups; saving the
     same groups again writes none.
   *Done, with deviation:* access loss is asserted on `/review/<id>` only.
   Search filters through the same `ACL` constant (`app/search.py` line 58),
   so a search fixture would test nothing extra. Reordering the same groups
   counts as a change and is logged (harmless).
3. **`ingest()` reads the source's groups under lock.** `app/ingest.py`:
   - `def ingest(source_id, external_id, title, data)`: drop `acl_groups`
     (line 62);
   - line 71, the unchanged-checksum branch: `update documents set
     acl_groups=(select acl_groups from sources where id=%s for share),
     deleted_at=null where id=%s`;
   - lines 89–93: rewrite as `insert into documents(…, acl_groups) select
     %s, …, s.acl_groups from (select acl_groups from sources where id=%s
     for share) s on conflict … returning id`. If no row comes back, the
     source was deleted mid-ingest: raise `LookupError` as line ~66 does.

   → verify by `pytest tests/test_ingest.py`, extended:
   - **lock ordering:** in one connection, open a transaction that runs
     `select … from sources where id=%s for update` and `update sources set
     acl_groups='{new}'`. Run `ingest()` in a thread: assert it is still
     running after 0.5 s, commit the first connection, join the thread, and
     assert the document has `{new}`;
   - a source deleted between triage and insert raises `LookupError`.

   Traps:
   - Update every `ing.ingest(…, acl)` call in `tests/test_ingest.py` (11)
     and `tests/test_hardening.py` (1). Where a test asserted the groups
     from the argument, set `sources.acl_groups` instead and assert that.
   - Do not move Docling, S3 or the LLM inside either transaction.
   *Done:* the four `ingest(...)` call sites in `app/crawl.py` lost their
   `acl` argument here (planned for step 4) so the crawlers keep running
   between steps.
4. **Crawlers write the current groups.** `app/crawl.py`:
   - drop `acl_groups` from the start selects (lines 31, 179, 349) and the
     `acl` argument from the `ingest` calls (lines 62, 226, 399, 416);
   - S3 restamp (line 76): `update documents d set acl_groups=s.acl_groups,
     deleted_at = case … end from (select acl_groups from sources where id=%s
     for share) s where d.source_id=%s`, keeping the `deleted_at` logic
     exactly;
   - SharePoint (line 282) and Confluence (line 484): `update documents d
     set acl_groups=s.acl_groups from (select acl_groups from sources where
     id=%s for share) s where d.source_id=%s and d.acl_groups is distinct
     from s.acl_groups`;
   - the docstrings at lines 167 and 337 say the groups are read at write
     time.

   → verify by `pytest tests/test_ingest.py tests/test_crawl_sharepoint.py
   tests/test_crawl_confluence.py`, extended. One test per crawler: a wrapper
   around `app.crawl.ingest` changes `sources.acl_groups` (as the save does,
   including the documents update) after the first file. After the crawl,
   every document of the source has the new groups.
   Traps:
   - `test_source_acl_change_applies_to_existing_docs` in
     `test_crawl_sharepoint.py` (line 211) must still pass unchanged.
   - SharePoint and Confluence run the restamp on an autocommit connection
     (`lock`), so it must stay a single statement.
5. **Audit page.**
   - `app/audit.py` after line 26: `acl_changes` = changed_at, the source
     name (or "(deleted source)"), old, new, changed_by, latest 100. Pass it
     to the template.
   - `app/templates/audit.html`: an "Access group changes" table after the
     class changes (line 21–23), same shape, groups joined with ", ".

   → verify by `pytest tests/test_audit.py` (or the file that covers
   `/admin/audit`), extended: a change shows on the page. Then the full
   suite.

## Tests

- `docker compose build app && docker compose run --rm app pytest` is green.
- The new tests prove:
  - a removed group loses access on save;
  - no crawler can write back old groups;
  - a concurrent ingest waits for the save and then writes the new groups;
  - the log is written once per change and can't be edited by the app.

## Rollback

- Revert the PR. `source_acl_changes` is a new table and can stay. No
  existing data changes shape.
- Without a revert, the old lag simply returns; nothing becomes wider.
