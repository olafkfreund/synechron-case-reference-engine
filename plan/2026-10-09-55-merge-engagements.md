---
status: approved
issue: 55
spec: spec/2026-10-09-55-merge-engagements.md
---

# Plan: Merge related contracts into one engagement reference

Line numbers are from `origin/main` at 8538b42, which includes #40, the
reviewer add/remove of list items. **Step 8 needs #64 on main.** Rebase onto
main after #64 merges, and before starting step 8.

## Approved decisions (self-contained)

- **Who merges:** a reviewer, by hand. There are no suggestions and nothing
  is automatic.
- **Same client is the only hard rule.** Every member's `client_mention`
  must resolve through `anonymise.resolve()` (`app/anonymise.py:83-86`) to
  the same **non-null** registry id.
  - An unregistered client blocks the merge; the reviewer registers it first.
  - `cases.client_id` is not used for this check, because it is only set at
    approval.
- **Data model.** A merged reference is a `cases` row with:
  - `document_id` null;
  - `member_count` N (at least 2);
  - `basis 'engagement'`.

  Each member is an ordinary single-document case with `merged_into` set to
  the merged row. Members keep their own `data` and status. There is no
  nesting.
- **Need-to-know.** A case is visible only if **every** document behind it
  is live and open to the user, and the case is not currently merged into
  another. This is the `VISIBLE` predicate:
  - it replaces `ACL` everywhere, and `ACL` is deleted;
  - `REVIEWABLE` is the same predicate without the `deleted_at` test. It is
    used only to list and show cases on review pages, so a merged case with
    a withdrawn member can still be un-merged;
  - if a member is hard-deleted, the stored `member_count` no longer
    matches, and the merged case is hidden everywhere.
- **Approval.** A merged case starts as `extracted` and needs a fresh
  approval. Approval is refused while any member is `rejected`.
- **Building the merged record.** It is combined from fields that are
  already checked. No model is called.
  - Text fields come from one member each. Where members differ, the
    reviewer picks which one with a radio button. The default is the case
    the merge started from.
  - `duration_months`, `team_size` and `period` are each taken whole from
    one chosen member. They are never summed or spanned across members.
  - `capabilities` and `tech_stack` are the union of all members,
    de-duplicated on the case-folded value; the first occurrence wins.
  - `outcomes` is always `[]`.
  - `summary` is `""`, with the note "merged from N contracts: write a
    summary".
  - `organisations` is the union of all members.
  - `basis_reason` is `"merged: "` plus the members' distinct reasons.
- **Quote provenance.** `Sourced`, `Outcome` and `Period` gain
  `document_id`, hidden from the LLM. `check()` verifies each item against
  its own document's text.
  - **New since the spec (#40 merged after spec approval):** an item with no
    `document_id` in a merged case, which is what a reviewer's Add creates,
    gets the id of the first member, in member order, whose text sources it.
    If no member's text sources it, it is unsourced.
  - An item with an id that isn't a member's is unsourced.
  - Single-document cases are unchanged.
- **Un-merge** works from any status. The members get `merged_into = null`
  and `status 'extracted'` (approval cleared), so each needs a fresh
  approval. The merged row gets `rejected` and is kept, because
  `generations.case_ids` and `research.case_id` still point at it. There is
  no "remove one member" action in v1: un-merge, then merge the rest again.
- **A member changes or is retired.** The helper `reopen_merged(conn,
  doc_ids)` sets an `approved` merged case back to `extracted`.
  - `ingest()` calls it on every changed document.
  - #64's flag-off path calls it for the documents it retires.
  - A withdrawn member hides the merged case through `VISIBLE`; the crawlers
    need no hook.
- **The UI is server-rendered forms only, with no JavaScript.** Rejecting a
  merged case is replaced by Un-merge.

## Steps

1. **Schema.** In `sql/schema.sql`, after the `basis` block (lines 72-75),
   add the block from the spec:

   ```sql
   -- a merged engagement (#55) has no document of its own; its member cases point to it
   alter table cases alter column document_id drop not null;
   alter table cases add column if not exists merged_into bigint references cases(id) on delete set null;
   alter table cases add column if not exists member_count int;
   alter table cases drop constraint if exists cases_merge_check;
   alter table cases add constraint cases_merge_check check (
     (document_id is null) = (member_count is not null)
     and (member_count is null or member_count >= 2)
     and (merged_into is null or document_id is not null));
   create index if not exists cases_merged_into_idx on cases (merged_into);
   ```

   In `tests/test_db.py`, after `test_case_basis_defaults_to_delivered_and_is_checked`
   (line 61), add `test_merge_columns_are_checked`:
   - `db.init()` twice;
   - inserting a merged row with `member_count 1` fails with
     `CheckViolation`;
   - a row with `document_id` null and `member_count` null fails;
   - a row with `document_id` null and `merged_into` set (a merged case
     inside a merged case) fails.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_db.py`.
   Traps:
   - Compose has no bind mount, so always build before pytest.
   - `drop not null` is already idempotent; don't wrap it.
   - The dev stack is live, and the web and worker run this migration at
     start. Every existing row must satisfy the constraint (its
     `document_id` is set and both new columns are null).


   *Done (coder):* `tests/test_db.py` 8 passed. *Deviation:* each
   failing insert in `test_merge_columns_are_checked` runs inside a
   savepoint and rolls back to it, because Postgres aborts the transaction
   after the first error and the next insert would fail for the wrong
   reason.

2. **VISIBLE replaces ACL.** In `app/review.py:22-23`, replace `ACL` with:

   ```python
   # need-to-know: every document behind a case must be live and open to the user; a member is
   # never shown on its own while merged (its content is inside the merged case)
   VISIBLE = ("c.merged_into is null and coalesce(c.member_count, 1) = (select count(*) from cases m "
              "join documents md on md.id = m.document_id where (m.id = c.id or m.merged_into = c.id) "
              "and md.deleted_at is null and md.acl_groups && %s::text[])")
   # review pages only: a withdrawn member must not trap its merged case (it can still be un-merged)
   REVIEWABLE = VISIBLE.replace("md.deleted_at is null", "(md.deleted_at is null or c.member_count is not null)")
   ```

   *Deviation:* the first draft dropped the `deleted_at` test for every case,
   which made a withdrawn single-document case reappear on the review pages and
   broke `test_reviewer_without_document_access_sees_nothing`. Only merged cases
   (`member_count` not null) now keep a withdrawn member visible there.

   Then change every caller (each takes the same single groups parameter):
   - `app/review.py:38-47` `load()`: the main select becomes `from cases c
     left join documents d on d.id = c.document_id where c.id = %s and
     {VISIBLE} and {OPEN} ...`, and the 404/409 probe uses `{VISIBLE}`.
     Step 4 changes what it returns.
   - `app/review.py:98-107` `review_list`: both queries use `left join
     documents d` and `{REVIEWABLE}`, and select `c.member_count`.
   - `app/review.py:116-120` `review_detail`: `left join documents d`, `left
     join sources s`, `{REVIEWABLE}`, and select `c.member_count`.
   - `app/search.py:7` import; `:58` `{VISIBLE}`; `:71-72` remove `join
     documents d on d.id = c.document_id`.
   - `app/render.py:20` import; `:375-376` remove the documents join and use
     `{VISIBLE}`.
   - `app/research.py:20` import; `:107-108` `from cases c where c.id = %s
     and ... and {VISIBLE}`.

   → verify by `grep -rn "\bACL\b" app/` (no output), then
   `docker compose build app && docker compose run --rm app pytest`: the
   existing need-to-know tests pass unchanged (`test_review.py:149`, `test_search.py:43`,
   and the render and research access tests).
   Traps:
   - **Every ACL caller:** there are 9 uses in 4 files (`grep -rn "ACL"
     app/`). Deleting `ACL` makes a missed one fail at import.
   - Don't touch `app/main.py:74` or `app/sources.py:90`; they are copies of
     groups, not checks.
   - The predicate needs the alias `c` for `cases`.
   - In `review_detail`, a merged case has no `d.title`, `d.external_id` or
     `s.name` (nulls). Step 5 renders the members instead.

3. **Quote provenance.**
   - In `app/schema.py`, add `document_id: SkipJsonSchema[int | None] =
     None` to `Sourced` (line ~50), `Outcome` and `Period`, after
     `unsourced`. Comment: "the member document this quote is from, in a
     merged case (#55); never from the LLM".
   - In `app/extract.py:78-102`, change `check(case, text)` to take `text:
     str | dict[int, str]`. With a dict, `mark()` picks the text like this:

     ```python
     def text_for(item):
         if isinstance(text, str):
             return text
         if item.document_id is None:  # a reviewer's Add (#40): the first member whose text sources it
             item.document_id = next((d for d, t in text.items() if sourced(<value>, item.source_quote, t)), None)
         return text.get(item.document_id, "")  # unknown or unmatched id: nothing vouches, so unsourced
     ```

     Thread `<value>` through `mark(item, value, literal)` as it is today.
     Period uses the same lookup.

   In `tests/test_extract.py`, add tests with made-up texts A and B:
   - an item stamped A with its quote in A → sourced;
   - stamped A with its quote only in B → unsourced;
   - stamped with an unknown id → unsourced;
   - unstamped with its quote in B → sourced and stamped B;
   - the string form → unchanged (existing tests).

   In `tests/test_schema.py`: `llm_schema()` has no `document_id`.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_extract.py tests/test_schema.py tests/test_review.py`.
   Traps:
   - The extraction prompt and `assemble()` never set `document_id`.
   - `model_dump()` stores `document_id: null` for old cases. That is
     harmless, and it changes `md5(data)` only when a case is re-saved.
   - `quotes()` and `summary_sourced()` need no change.

4. **Merge (server).** In `app/review.py`:
   - **`load()`** returns `(case, texts)`. `texts` is the string as today for
     a single-document case. For a merged case it is `{document_id:
     text[:MAX_CHARS]}` for its members, read with `select m.document_id,
     md.text from cases m join documents md on md.id = m.document_id where
     m.merged_into = %s order by m.id` after the lock. `edit`, `approve`
     and `reject` pass it to `check()` unchanged.
   - **`combine(members: list[tuple[int, int, ReferenceCase]], pick:
     dict[str, int]) -> ReferenceCase`** is pure. Each member is `(case_id,
     document_id, case)`, and `pick` maps a field to a case id.
     - It deep-copies each item, setting `document_id`.
     - It follows the decision rules above for scalars, `duration_months`,
       `team_size`, `period`, the lists, `outcomes`, `summary`,
       `organisations`, `needs_attention`, `basis` and `basis_reason`.
   - **`candidates(conn, cid, user)`** returns the other cases that are:
     - `{VISIBLE}` to the user;
     - `basis 'engagement'`, `document_id` not null, `status <>
       'rejected'`;
     - whose `client_mention` resolves to the same non-null id as case
       `cid`.

     Each is returned as `(id, title, document title, period)`.
   - **`POST /review/merge/preview`** (`members: list[str]`, each
     `"<case id>:<version>"`, the starting case first). It renders `review_merge.html` (step 5) with the fields that
     differ.
   - **`POST /review/merge`** (`members`, plus one `pick_<field>=<case
     id>` per differing field). In one transaction it:
     1. locks every member with `select ... from cases c where c.id =
        any(%s) and {VISIBLE} for update of c`;
     2. checks: the count equals `len(set(ids)) >= 2` (ids parsed from `members`; a malformed one → 400); each version matches;
        each member is an engagement, unmerged, has a document and is not
        rejected; all members resolve to the same non-null client;
     3. calls `combine()`, then `check(case, texts)`;
     4. inserts `cases(document_id, member_count, basis, status, data,
        summary, search_text)` with `(null, N, 'engagement', 'extracted',
        ...)`;
     5. runs `update cases set merged_into = <new id> where id = any(ids)`;
     6. redirects with 303 to `/review/<new id>`.

     It returns 404 if any member isn't visible, 409 for a stale version or a
     member that is already merged, and 400 for a different or unregistered
     client, a delivered or rejected member, fewer than 2 members, or a
     `pick_` id that isn't a member.

   In `tests/test_review.py`, extend `make()` (line 35) with `basis` and
   `mention` parameters, then test:
   - a successful merge: lists unioned with the quotes and `document_id`
     kept, the picked scalars, empty summary with the note, no outcomes;
   - members leave the list and the detail page (404);
   - every refusal listed above.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_review.py`.
   Traps:
   - Made-up test data only (an "Acme" client in the registry, `DOC`-style
     texts).
   - **`make()` teardown deletes sources (line 52),** which cascades to
     member cases but not to merged rows. Delete merged rows first (`delete
     from cases where document_id is null and id in (...)`), or the
     cascading member delete sets `merged_into` null and leaves the merged
     row orphaned.
   - Every route requires the `reviewer` role.
   - `members` is a list; de-duplicate by id, as `generate()` does
     (`app/render.py:353`).


   *Done (coder):* `tests/test_review.py` 24 passed. *Deviations:*
   - A member that is already merged is hidden by `VISIBLE`, so a plain
     lock would give 404. After a count mismatch, `lock_members()` probes
     with `VISIBLE` minus the `merged_into is null` test: if all members
     are found, it's 409; otherwise 404.
   - `POST /review/merge` is `async def`, so it can read the dynamic
     `pick_<field>` form fields. The database work runs in the threadpool.
   - The preview route is untested until step 5 adds `review_merge.html`.

5. **Merge UI** (templates, no JavaScript):
   - **`app/templates/review_detail.html`, single engagement case with a
     resolvable client:** a form posting to `/review/merge/preview`, with a hidden
     `members="<id>:<v>"` for this case and one checkbox per candidate
     (`name="members"`, value `"<id>:<v>"`). One value per member avoids
     parallel lists getting out of order.
   - **New `app/templates/review_merge.html`:**
     - the members' document titles;
     - a table with one row per differing field: the field label, then one
       radio button per member (`name="pick_<field>"`, value the case id,
       the first checked), showing that member's value and quote;
     - hidden `members` (`"<id>:<v>"`);
     - a "Merge" submit button.
   - **`review_detail.html`, merged case:**
     - the line-4 "Document:" paragraph becomes a members table: title,
       source, `external_id`, basis reason, status, and "withdrawn" when
       `deleted_at` is set;
     - each field row shows its document title next to the quote. `rows()`
       adds `doc=<title>` from a `{document_id: title}` map that
       `review_detail` passes in.
   - **`app/templates/review_list.html`:** a merged row shows "N contracts"
     in the "Source document" column.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_review.py`
   with these tests:
   - the candidate form lists only same-client engagements;
   - the preview shows one radio group per differing field;
   - the merged detail page shows the members table and the per-row document;
   - the list row shows "2 contracts";
   - `<script` is absent from the three pages.

   Traps:
   - No JavaScript, and no `onclick`.
   - Autoescape is on; don't use `|safe`.
   - Show the form only when `reviewable`, as the existing Save buttons do.

6. **Approve guard and un-merge.** In `app/review.py`:
   - **`approve` (line 188):** for a merged case, before saving, `select
     count(*) from cases where merged_into = %s and status = 'rejected'`.
     If it is above 0, return 409 "a member contract is no longer an
     engagement: un-merge".
   - **`POST /review/{cid}/unmerge`** (`v`):
     1. lock with `{REVIEWABLE}`, `document_id is null`, and `{VERSION} =
        %s`, `for update of c`. This is not `load()`: no `OPEN`, and
        withdrawn members are allowed;
     2. `update cases set merged_into = null, status = case when status =
        'rejected' then 'rejected' else 'extracted' end,
        approved_by = null, approved_at = null, review_due = null where
        merged_into = %s`;
     3. `update cases set status = 'rejected', approved_by = null,
        approved_at = null, review_due = null where id = %s`;
     4. redirect with 303 to `/review`.
   - **`reject`** returns 400 "un-merge instead" for a merged case.
   - **`review_detail.html`:** for a merged case, the Reject form becomes
     an Un-merge form posting to `/review/{id}/unmerge`. It shows whenever
     the case is visible, not only when it is `reviewable`.

   Tests:
   - un-merge from `extracted` and from `approved` → members back in the
     list as `extracted`; the merged row is `rejected` and gone from list,
     detail, search and generate;
   - approve refused with a rejected member;
   - reject on a merged case → 400.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_review.py`.
   Traps:
   - A withdrawn member makes `load()` 404 (under `VISIBLE`) while the page
     still shows (under `REVIEWABLE`). Hide Save, Approve and Add when any
     member is withdrawn, so the page doesn't offer buttons that 404.

7. **Member change and retire.** In `app/ingest.py`, add:

   ```python
   def reopen_merged(conn, doc_ids: list[int]) -> None:
       """A merged engagement whose member document changed or was retired goes back to review (#55)."""
       conn.execute("update cases set status = 'extracted' where status = 'approved' and id in "
                    "(select merged_into from cases where document_id = any(%s) and merged_into is not null)",
                    (doc_ids,))
   ```

   Call it at `app/ingest.py:101-102`, right after the `update cases set
   status=%s where document_id=%s`, with `[doc_id]`.

   In `tests/test_ingest.py`, with the `env` fixture:
   - a changed member document reopens its approved merged case;
   - a changed member with no basis is `rejected`, the merged case is
     reopened, and approval is then refused (step 6);
   - an unrelated document leaves merged cases alone.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_ingest.py`.
   Traps:
   - The unchanged-checksum early return (`:70-72`) must **not** reopen
     anything, because nothing changed.
   - Don't touch `merged_into` here. The member stays merged until a
     reviewer un-merges.

8. **#64 hookup** (after #64 is on main). In `app/sources.py`:
   - append `returning c.document_id` to `RETIRE_FLAGGED`;
   - in `update()`, at the flag-off branch, change
     `conn.execute(RETIRE_FLAGGED, (sid,))` to
     `reopen_merged(conn, [r[0] for r in conn.execute(RETIRE_FLAGGED, (sid,))])`;
   - import it with `from app.ingest import reopen_merged`.

   In `tests/test_sources.py`, extend
   `test_unticking_executed_retires_flagged_engagements`: a merged
   engagement (approved) with one member that has reason "source marked
   executed" → after unticking, that member is `rejected`, the merged case is
   `extracted`, and approval is refused.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_sources.py`.

   *Done (coder, committed):* the extended test passes. Un-merge later got
   a fix so a retired member stays `rejected` (see *Review*).
   Traps:
   - Check #64's merged constant names and line numbers on main first. If
     they differ from #64's plan, update this step in the same commit as the
     code.
   - **Import cycle:** `app.ingest` must not import `app.sources` or
     `app.review`.
   - The test teardown deletes merged rows before sources (step 4 trap).

9. **Need-to-know matrix.** In `tests/test_review.py`, add one parametrised
   test over list, detail, edit, approve, search, generate (md),
   research from-case, and research send. It uses a merged case with member
   documents in groups `g-a` and `g-b`:
   - a user with only `g-a` → the merged case is absent or 404, and the
     members are absent or 404;
   - a user with `g-a` and `g-b` → the merged case is present and the
     members are absent;
   - member B withdrawn (`deleted_at`) → absent from search, generate and
     research, and editing and approval return 404; still present in the
     review list and detail, where Un-merge works;
   - member B hard-deleted → absent everywhere, including review.

   → verify by the full suite (see Tests).
   Traps:
   - The search and generate cases need `status 'approved'` and a future
     `review_due` on the merged row.
   - Research uses `visible_case` (`app/research.py:105`); call the routes,
     not the helper.
   - The `make()` comment at line 36: don't call `db.init()` after `make()`.

## Review

*Review:* fixes applied after the Opus review of steps 1-9:
1. **Blocker.** Un-merge (`app/review.py`) kept resetting every member to
   `extracted`, reviving a member that #64 had retired. It now uses
   `status = case when status = 'rejected' then 'rejected' else 'extracted' end`.
   Test: `test_unticking_executed_retires_flagged_engagements` un-merges and
   the flagged member stays `rejected`. **Step 6's un-merge SQL (item 2 of the
   un-merge route) is corrected accordingly.**
2. **Approve guard** also refuses when any member's `basis <> 'engagement'`
   (`test_approve_refused_when_a_member_is_no_longer_an_engagement`).
3. **Merge lock:** `order by c.id` before `for update of c`, so overlapping
   merges cannot deadlock.
4. **Need-to-know:** `test_one_visible_member_cannot_merge_unmerge_or_list_the_other`
   covers merge preview, merge, un-merge and the candidate list for a user who
   sees only one member's document.

*Accepted risk:* if a member's document is hard-deleted, the merged case can't
be un-merged through the app and the other member stays hidden. Nothing in the
app hard-deletes documents today, so this is documented, not fixed.

## Tests

- `docker compose build app && docker compose run --rm app pytest`: green.
  This is the existing suite plus the new tests in `test_db`, `test_extract`,
  `test_schema`, `test_review`, `test_ingest` and `test_sources`.
- `grep -rn "\bACL\b" app/` → no output.
- `grep -rn "<script\|onclick" app/templates/` → no new matches.
- **Manual,** in the running local stack. Run `docker compose build app &&
  docker compose up -d app worker` only if the user agrees; never `down`.
  1. Log in as the test reviewer (#71) and upload two made-up executed
     contracts for a registered made-up client.
  2. Merge them, pick fields, write a summary and approve.
  3. Find the result in search and generate a docx: the engagement line is
     present and there are no Outcomes.
  4. Un-merge: both members are back in the queue.

## Rollback

- **Code:** revert the PR.
- **Schema:** the new columns, constraint and index are additive. Reverted
  code ignores them, but merged rows (`document_id is null`) would then be
  invisible, and their members visible again. To remove the schema fully,
  run this once:

  ```sql
  update cases set merged_into = null, status = 'extracted', approved_by = null,
    approved_at = null, review_due = null where merged_into is not null;
  delete from cases where document_id is null;
  alter table cases drop constraint if exists cases_merge_check;
  drop index if exists cases_merged_into_idx;
  alter table cases drop column if exists merged_into, drop column if exists member_count;
  alter table cases alter column document_id set not null;
  ```

  Delete merged rows before `set not null`, because it fails while they
  exist. `research.case_id` is `on delete set null`.
- **Step 8 alone:** revert the `sources.py` change. #64 then retires members
  without reopening their merged case, but approval is still refused while a
  member is `rejected` (step 6).

## Coder handoff

This plan has 9 steps, 8 of which edit files. It touches `sql/schema.sql`,
`app/review.py`, `app/search.py`, `app/render.py`, `app/research.py`,
`app/schema.py`, `app/extract.py`, `app/ingest.py`, `app/sources.py`, three
templates and six test files. That is well over the threshold of 3 steps or
3 files.

- **Steps 1-7 and 9 go to one `coder` agent** (Sonnet). Start it with this
  plan's path and step 1, and send each later step with `SendMessage`.
- **Step 8** goes to the same coder once #64 is on main and the branch is
  rebased.
- The session model reviews with a fresh Opus agent, given only this plan
  and `git diff`, and does the manual run.

*Steps 6, 7 and 9 done (coder, one commit):* the full suite passed twice
(456 passed). Test-support changes beyond the plan:
- the `acme` fixture clears `cases.client_id` before deleting its client,
  because approving a merged case links the client;
- the matrix has its own `ver_or_none()`, because a hard-deleted member has
  no row to hash.

Step 6's un-merged row isn't tested in search or generate directly: its
`rejected` status excludes it, and step 9's matrix covers those routes.
Step 8 waits for #64 (PR #82).
