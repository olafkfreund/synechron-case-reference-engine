---
status: draft
issue: 190
intent: intent/2026-10-12-190-merged-member-new-facts.md
---

# Spec: A merged case does not pick up a re-extracted member's new facts

## Design

The user decided this (intent, 2026-10-12):

- No automatic rebuild.
- The merged case page says when a member changed or was withdrawn since the
  merge, and offers a one-step re-merge.
- The reviewer's choices and edits stay until the reviewer uses it.

### 1. Remember what each member looked like when it was merged

`sql/schema.sql:83-91` (the #55 block) gains:

```sql
alter table cases add column if not exists merged_version text;  -- md5(data) of a member when merged (#190)
update cases set merged_version = md5(data::text) where merged_into is not null and merged_version is null;
```

- **When it's set:** `do_merge` (`app/review.py:383-400`) sets
  `merged_version = md5(data::text)` in the same statement that sets
  `merged_into`.
- **What "changed since merge" means:** for a member,
  `md5(m.data::text) is distinct from m.merged_version`. This is the same
  expression as `VERSION` (`app/review.py:39`), applied to the member row.
- **When it flips:** it compares the member's data, not the document's
  checksum. So it flips only once the re-extraction has landed (the upsert in
  `app/extract.py:163-168`), and a re-merge never picks up the old facts.
  Members are hidden while merged (`VISIBLE`, `:34`), so nothing else
  changes their data.
- **Existing merges:** the backfill marks them unchanged. A member that
  changed before this release is not flagged. Its merged case was already
  reopened by `reopen_merged`, and it can still be un-merged.

### 2. Show it on the merged case page

- **Query:** `review_detail` (`app/review.py:130-160`) adds one column to the
  members query (`:146-148`): `md5(m.data::text) is distinct from
  m.merged_version`.
- **Badge:** `templates/review_detail.html:28` shows a "changed since merge"
  badge next to the existing "withdrawn" badge.
- **Re-merge card:** shown when a member is changed or withdrawn and the
  reviewer can see the case. It says:
  - "A contract changed or was withdrawn since this merge. The facts below
    are from the merge."
  - Below that, a POST form to `/review/{id}/remerge` with the hidden `v`,
    and the button **Re-merge with the current contracts (replaces the edits
    on this merged case)**.
- **When it's hidden:** it shows nothing when fewer than two live members
  would remain. The existing un-merge button covers that case.
- **No JavaScript.** CSRF comes from the existing origin check on every POST
  (`app/main.py:132`).

### 3. `POST /review/{cid}/remerge`

The route sits in `app/review.py` next to `unmerge` (`:403-419`), requires
the reviewer role, and runs in one transaction. It reuses the merge code and
copies none of it.

1. **Lock the members first, then the merged case.** Lock the members with
   `select ... where merged_into = %s order by id for update`, as `unmerge`
   does (#144). Then lock the merged case with `REVIEWABLE` and
   `document_id is null ... for update of c`.
   - If the reviewer can't see it, return 404.
   - If `VERSION != v`, return 409 "case changed; reload the page".
   - This is the same order as ingest, extract and unmerge: source, then
     documents, then cases, with members before the merged case. The route
     takes no source or document lock.
2. **Load the members** with their document text, `deleted_at`, status and
   basis.
   - A live member that is rejected or no longer an engagement: 409 with the
     message `approve` uses (`:229-232`), "a member contract is no longer an
     engagement: un-merge".
   - Fewer than two live, non-withdrawn members: 409 "fewer than 2 contracts
     remain: un-merge".
3. **Check the client.** All live members must still resolve to the same
   registered client. The check moves out of `lock_members` (`:352-356`)
   into a small helper, `same_client(conn, cases)`, which both routes call.
   Otherwise: 400 "the contracts must be for the same registered client".
4. **Recover the reviewer's choices from the merged data.** Every field
   `combine` builds carries the `document_id` of the member it came from
   (`stamped`, `:287-290`). So for each field in `MERGE_FIELDS` (`:271`),
   use the live member whose `document_id` matches the merged case's
   current value. If no live member matches (it was withdrawn), fall back to
   `combine`'s own default, `first_sourced`. Nothing new is stored.
5. **Rebuild and save the merged case:**
   - `case = combine(live, pick)`, then
     `check(case, {doc_id: text[:MAX_CHARS]})`, then
     `save(conn, cid, case)` (`:81-84`);
   - on the merged row: `status='extracted'`, `approved_by`, `approved_at`
     and `review_due` set to null, and `member_count = len(live)`;
   - on withdrawn members: `merged_into = null`, so they leave the merge and
     `VISIBLE`'s member count still matches;
   - on live members: `merged_version = md5(data::text)`.
6. **Redirect** with a 303 to `/review/{cid}`. The merged row's id is kept,
   because generations and research point at it.

### 4. Docs

`docs/user-guide/reviewers.md` ("Merge contracts", `:108-125`) gets one step:
"If a contract changes or is withdrawn after the merge, the merged case shows
it. Press **Re-merge with the current contracts** to rebuild it from the
current versions. Your field choices are kept, your edits are not, and the
case needs a new summary and approval."

## Alternatives rejected

- **Rebuild automatically when the member's re-extraction lands.** The user
  rejected it: it throws away the reviewer's edits unseen.
- **Mark changes by document checksum** (store it at merge, compare with
  `documents.checksum`). It flips at ingest, before the extract job runs, so
  a re-merge in that window would rebuild from the old facts.
- **A re-merge that goes through the preview page.** That's two steps, and
  the preview's member form (`lock_members`) needs `VISIBLE`, which hides
  merged members. Recovering the choices from the `document_id` stamps keeps
  it to one step, with no form state.
- **Keep the reviewer's summary across a re-merge.** It describes the old
  facts, and `fix_summary` checks quotes, not meaning. The new case gets the
  merge note "write a summary", as a fresh merge does.
- **Store the per-field choices in a new column.** The stamps already hold
  them.

## Risks

- **Lost edits on re-merge.** The button text says so, and it is the user's
  decision.
- **Deadlock.** Locking the merged case before its members would deadlock
  with `reopen_merged` (#144). Step 1 keeps the members-first order, and a
  test checks it.
- **Backfill of existing merges.** A member that changed before this release
  shows as unchanged. That's acceptable: the merged case was already
  reopened, and un-merge still works.
- **A member hidden from the reviewer by its access groups.** `REVIEWABLE`
  already returns 404 for the whole merged case, so the re-merge can't touch
  it.
- **Extra page cost.** One md5 per member row on the detail page. A case has
  only a few members.

## Verification

New tests in `tests/test_review.py`, built on the existing `_merged`,
`merge`, `eng` and `ver` helpers (`:440-462`). All of them fail on main,
which has no `merged_version` column, no badge and no route:

1. `test_member_change_shows_on_merged_case_and_remerge_takes_new_facts`:
   - merge a and b, with industry picked from b;
   - update b's data (a new tech item) and its document text, as a
     re-extraction would;
   - the detail page shows "changed since merge" and the re-merge form;
   - POST re-merge returns 303;
   - the merged data has the new tech item, industry still comes from b's
     document, the status is "extracted", and the page no longer shows the
     badge.
2. `test_unchanged_merged_case_offers_no_remerge`: a fresh merge shows
   neither the badge nor the form.
3. `test_remerge_drops_a_withdrawn_member`: with three members and one
   document deleted, re-merge leaves `member_count` at 2 and the withdrawn
   member's `merged_into` null, and the case is still listed.
4. `test_remerge_refusals`:
   - a stale `v` returns 409;
   - one live member left returns 409;
   - a rejected member returns 409;
   - a member that now resolves to another client returns 400;
   - a non-reviewer gets 403;
   - a reviewer outside the groups gets 404.
5. `test_remerge_locks_members_before_the_merged_case`: a copy of
   `test_unmerge_locks_members_before_the_merged_case` (`:723`).
6. `test_merge_records_member_versions`: after `do_merge`, each member's
   `merged_version` equals `md5(data::text)`.

Then run the full suite: `docker compose build -q app && docker compose run
--rm app timeout 900 pytest -q -p no:cacheprovider`. Finally, check the
merged case page by hand in the local demo: the badge, the note and the
button render without JavaScript.
