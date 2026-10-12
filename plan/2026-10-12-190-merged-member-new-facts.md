---
status: approved
issue: 190
spec: spec/2026-10-12-190-merged-member-new-facts.md
---

# Plan: A merged case does not pick up a re-extracted member's new facts

## Decisions

These are carried over from the approved spec, which was checked against HEAD
`bf3f470`. The line numbers below are corrected where the spec was off.

- **No automatic rebuild.** The merged case page says when a member changed
  or was withdrawn since the merge, and offers a one-step re-merge. The
  reviewer's choices and edits stay until they press it.
- **The marker.** A new member column, `cases.merged_version`, holds
  `md5(data::text)` of the member when it was merged.
  - A member has changed when `md5(m.data::text) is distinct from
    m.merged_version`.
  - This flips only once the re-extraction upsert has landed
    (`app/extract.py:163-168`), never at ingest.
  - Existing merges are backfilled as unchanged.
- **The re-merge.** It keeps the merged row's id and reuses `combine`,
  `check`, `save` and `first_sourced`.
  - The per-field choices are recovered from the `document_id` stamps in the
    merged data.
  - Withdrawn members are dropped (`merged_into = null`).
  - The summary resets, because `combine` leaves it empty with a note.
  - The status becomes `extracted`, and the approval fields are cleared.
- **No JavaScript, Jinja only.** CSRF is handled by the existing origin check
  in `app/main.py:128-134`, so add no token.
- **Lock order:** source, then documents, then cases, and members before the
  merged case (#144), as `unmerge` does. The new route takes no source or
  document lock.

## Steps

1. **Tests that fail on main.** In `tests/test_review.py`, after `:780`, add
   the six tests below. Build them on the helpers `eng` (`:433`), `merge`
   (`:443`), `merged_of` (`:448`), `_merged` (`:453`), `ver` (`:59`),
   `client(R)`, and the fixtures `make` and `acme`.
   - `test_merge_records_member_versions`: each member's `merged_version`
     equals `md5(data::text)`.
   - `test_member_change_shows_on_merged_case_and_remerge_takes_new_facts`:
     - merge a and b with `industry` picked from b;
     - update b's `data` (add a tech item quoted from new text) and its
       document's `text`, as a re-extraction would;
     - GET the case: the page has "changed since merge" and
       `action="/review/{id}/remerge"`;
     - POST `/review/{id}/remerge` with `v=ver(id)` returns 303;
     - the merged data has the new tech item, `industry.document_id` is
       still b's document, and the status is `extracted`;
     - GET the case again: no "changed since merge".
   - `test_unchanged_merged_case_offers_no_remerge`: a fresh merge shows
     neither the badge nor the form.
   - `test_remerge_drops_a_withdrawn_member`:
     - merge three members and set one document's `deleted_at`;
     - the page shows the form;
     - after the re-merge, `member_count` is 2, the withdrawn member's
       `merged_into` is null, and the case still opens for `client(R)`.
   - `test_remerge_refusals`:
     - a stale `v` returns 409;
     - two members with one withdrawn return 409 ("fewer than 2");
     - a member with `status='rejected'` returns 409;
     - a member whose `data.client_mention.value` now names another
       registered client returns 400;
     - a non-reviewer gets 403;
     - a reviewer without the groups gets 404.
   - `test_remerge_locks_members_before_the_merged_case`: a copy of
     `test_unmerge_locks_members_before_the_merged_case` (`:723-745`),
     posting to `/remerge` and asserting `got == [303]` and
     `status_of(new) == "extracted"`.

   Verify by building the image (`docker compose build -q app`) and running
   the new tests with `docker compose run --rm app pytest -q
   -p no:cacheprovider tests/test_review.py -k "remerge or
   records_member_versions"`. All six must fail.

   Traps:
   - Rebuild the image before every test run. There is no bind mount.
   - A changed `data` changes `ver()`, so read `v` after the edit.
   - The test for another client needs a second client registered.

2. **Schema.** In `sql/schema.sql`, after `:91` (the `retired_by` line), add:

   ```sql
   alter table cases add column if not exists merged_version text;  -- md5(data) of a member when merged (#190)
   update cases set merged_version = md5(data::text) where merged_into is not null and merged_version is null;
   ```

   Verify that the suite's schema load runs twice without error. Any test
   run does this.

   Traps:
   - Use `add column if not exists`, and make the backfill idempotent
     (`where ... is null`).
   - No new constraint.

3. **Record the versions at merge.** In `app/review.py`, `do_merge`, change
   the update at `:399` to `update cases set merged_into = %s,
   merged_version = md5(data::text) where id = any(%s)`.

   Verify: `test_merge_records_member_versions` passes.

   Traps: none.

4. **Share the client check.** In `app/review.py`, move `:352-356` of
   `lock_members` (`registry`, `cases`, `clients`, and the 400) into a new
   function, `same_client(conn, cases: dict[int, ReferenceCase]) -> None`.
   Place it above `lock_members`, and have `lock_members` call it.

   Verify: `pytest tests/test_review.py -k merge` stays green, including
   `test_merge_refusals` (`:550`).

   Traps: keep the 400 message word for word.

5. **The detail page.** Two changes:
   - In `app/review.py`, `review_detail`, the members query at `:146`:
     - append `, md5(m.data::text) is distinct from m.merged_version` as
       column index 8;
     - compute `remerge = merged and any(m[6] or m[8] for m in members) and
       sum(not m[6] for m in members) >= 2`;
     - pass `remerge=remerge` in the `page(...)` call at `:157`.
   - In `app/templates/review_detail.html`:
     - at `:28`, after the withdrawn badge, add `{% if m[8] %} <span
       class="badge warn">changed since merge</span>{% endif %}`;
     - after the Decision block (`:44-58`), outside `{% if reviewable %}`,
       add a `section-card`, shown when `merged and remerge`;
     - the card has the note "A contract changed or was withdrawn since this
       merge. The facts below are from the merge.";
     - below the note, a `<form method="post"
       action="/review/{{ id }}/remerge">` with the hidden `v` and the
       button **Re-merge with the current contracts (replaces the edits on
       this merged case)**.

   Verify: the detail-page asserts in step 1's tests pass.

   Traps:
   - Jinja only, no script.
   - The members query runs only when `merged`, so `remerge` must be False
     otherwise.
   - Keep the existing column indexes 0–7.

6. **The route.** In `app/review.py`, after `unmerge` (`:403-419`), add
   `@router.post("/review/{cid}/remerge") def remerge(cid: int, v: str =
   Form(), user: User = Depends(require("reviewer")))`, using one
   `db.connect()`:
   1. Lock the members with `conn.execute("select 1 from cases where
      merged_into = %s order by id for update", (cid,))`.
   2. Lock the merged case with `select {VERSION}, c.data from cases c where
      c.id = %s and {REVIEWABLE} and c.document_id is null for update of c`,
      passing `(cid, list(user.groups))`.
      - No row: 404 "no such case".
      - `row[0] != v`: 409 "case changed; reload the page".
   3. Load the members with `select m.id, m.document_id, m.data, m.status,
      m.basis, d.text, d.deleted_at is not null from cases m join documents
      d on d.id = m.document_id where m.merged_into = %s order by m.id`.
      - `live` = the members whose document is not withdrawn.
      - If any live member is rejected or not an engagement: 409 "a member
        contract is no longer an engagement: un-merge".
      - If `len(live) < 2`: 409 "fewer than 2 contracts remain: un-merge".
   4. Build `cases = {id: ReferenceCase.model_validate(data)}` for the live
      members and call `same_client(conn, cases)`.
   5. Recover the choices. For each `f` in `MERGE_FIELDS`, take the
      document id from the merged data, `old = getattr(merged,
      f).document_id`, and set `pick[f]` to the live member whose document
      id equals `old`. Fields with no match are left out, so `combine` falls
      back to `first_sourced`.
   6. Rebuild and save:
      - `case = combine([(id, doc, cases[id]) for live], pick)`;
      - `check(case, {doc: text[:MAX_CHARS] for live})`;
      - `save(conn, cid, case)`.
   7. Update the rows:
      - the merged case: `update cases set status='extracted',
        approved_by=null, approved_at=null, review_due=null,
        member_count=%s where id=%s`;
      - the withdrawn members: `update cases set merged_into=null where id =
        any(%s)`;
      - the live members: `update cases set merged_version=md5(data::text)
        where id = any(%s)`.
   8. Return `RedirectResponse(f"/review/{cid}", status_code=303)`.

   Verify: all six step-1 tests pass.

   Traps:
   - Lock the members before the merged case (#144).
   - Never lock a source or document here.
   - `member_count` must equal the live members, or `VISIBLE` hides the
     case.
   - The `cases_merge_check` constraint requires `member_count >= 2`, and
     the "fewer than 2" 409 guarantees it.

7. **Docs.** In `docs/user-guide/reviewers.md`, "Merge contracts"
   (`:108-125`), add step 6:

   > If a contract changes or is withdrawn after the merge, the merged case
   > shows it. Press **Re-merge with the current contracts** to rebuild the
   > case from the current versions. Your field choices are kept, your edits
   > are not, and the case needs a new summary and approval.

   Verify: read it through.

   Traps: none.

## Tests

- `docker compose build -q app && docker compose run --rm app pytest -q -p no:cacheprovider tests/test_review.py -k "remerge or records_member_versions"`:
  6 pass. On main, 6 fail.
- `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`:
  the full suite passes (653 plus 6).
- By hand, in a local compose run: open a merged case, change a member's
  data, and check that the badge, the note and the button render and that
  the button works.

## Rollback

Revert the PR's merge commit. The `merged_version` column is then unused and
harmless. If wanted, drop it with `alter table cases drop column if exists
merged_version`. No data is lost: a re-merge only rewrote merged rows that a
reviewer chose to rebuild.

## Handoff

The plan has 7 steps that edit files, across 5 files: `tests/test_review.py`,
`sql/schema.sql`, `app/review.py`, `app/templates/review_detail.html` and
`docs/user-guide/reviewers.md`. That is over the threshold, so it goes to the
`coder` agent.
