---
status: approved
issue: 199
spec: spec/2026-10-12-199-best-practice-sources.md
---

# Plan: Case studies: cite the public best-practice sources used

## Approved decisions (from the spec, approved 2026-10-12)

- A reviewer attaches research claims to a case. The claims are **copied**
  into a new column `cases.public_sources jsonb not null default '[]'`.
  - The copy is never put in `data` or `search_text`.
  - Each item is `{quote, publisher, url, retrieved_at, type, research_id,
    attached_by, attached_at}`. The AI `statement` is never copied.
  - At most 20 items per case. Duplicates by `(url, quote)` are dropped.
- **Attach** comes from the research page, only for research that is:
  - the reviewer's own (`created_by = user.sub`);
  - finished (`status = 'done'`);
  - started from this case (`case_id = cid`).

  Otherwise it returns 404. A non-reviewer gets 403.
- The server copies claims **by index from the stored row**, never from the
  form. Each claim must pass `visible_claims` (`app/research.py:168`).
- **Remove** is done on the review page.
- Both attach and remove carry a token `s = md5(public_sources::text)`. A
  stale token returns 409 "case changed; reload the page".
- Attaching or removing **never changes `status`**, and never reopens an
  approved case (decided by the user, 2026-10-12). It is allowed on open and
  approved cases a reviewer can see (`REVIEWABLE`).
- **Output:** `/generate` combines the chosen cases' attached sources first,
  then the per-download research claims. It deduplicates them by
  `(url, quote)` and passes them through the existing `industry_section`,
  which runs `apply()` and then `blocked()` per claim at render time. No
  template changes.
- **Heading** (decided by the user, 2026-10-12):
  - `SOURCES_HEADING = "Best practice and public sources"` when at least one
    attached source is in the combined list;
  - otherwise the existing `INDUSTRY_HEADING = "Industry context (public
    sources)"`.

  Both are constants, so #23 can change them. The disclaimer is unchanged.
  The acknowledgement rule is unchanged: it is needed only when there is no
  case.
- **Merged cases:**
  - `do_merge` sets the merged row's `public_sources` to the union of its
    members' lists, deduplicated and capped at 20;
  - `remerge` and `unmerge` don't touch any list.
- **Not in scope:**
  - no `generations` column for which sources went out;
  - slides still show at most `SLIDE_ITEMS = 5`, with attached sources
    first.

## Steps

1. `sql/schema.sql:93` (after the #190 `merged_version` backfill): add
   ```sql
   -- public claims a reviewer accepted for this case (#199): copied, so they outlive the research row;
   -- never in data or search_text (research text must not reach search)
   alter table cases add column if not exists public_sources jsonb not null default '[]';
   ```
   → verify: `pytest tests/test_industry.py -q`. `db.init()` runs the
   schema, so it must stay idempotent.
   Traps: additive only. Never `drop` or `rename`. The migrate task runs it
   in staging and production.

2. `app/render.py:40-48`, then `generate()` at `:352-398`:
   - Add `SOURCES_HEADING = "Best practice and public sources"` next to
     `INDUSTRY_HEADING` (`:40`).
   - Give `industry_section(claims, clients)` the keyword
     `heading=INDUSTRY_HEADING`, and use it in the returned dict (`:68`).
   - In `generate()`, add `c.public_sources` to the case query (`:375`) and
     unpack it in the loop (`:385`). Collect `attached` across the cases in
     `ids` order. Then:
     ```python
     seen, merged = set(), []
     for c in attached + claims:  # attached first: they win the slide's 5 places
         if (k := (c.get("url"), c.get("quote"))) not in seen:
             seen.add(k); merged.append(c)
     industry = industry_section(merged, clients, heading=SOURCES_HEADING if attached else INDUSTRY_HEADING) \
         if merged else None
     ```
     This replaces `industry = ... if research_id is not None else None`
     (`:393`). The check `if not ids and not (industry and ...)` (`:396`)
     stays.
   - Audit `research_id if industry else None` (`:403`): keep it, but log
     the research id only when `research_id` was given. Use
     `research_id if (industry and claims) else None`, so attached-only
     outputs log no research.

   → verify: `pytest tests/test_industry.py tests/test_render.py -q`. The
   existing tests pass unchanged.
   Traps:
   - The heading must stay "Industry context (public sources)" when nothing
     is attached (`test_markdown_section_wording`).
   - Keep "Withheld before audit": render the body before the insert.
   - **#197** moves the code above into a shared `build()` on
     `feat/197-output-preview`. Whichever branch merges second rebases and
     moves its change into the other's shape: the change lives where
     `industry` is computed. Nothing else conflicts.

3. `app/research.py:179-202` (`research_view`) and
   `templates/research_view.html:15-24`:
   - **Index:** number the stored claims before filtering:
     `claims, omitted = visible_claims([{**c, "n": i} for i, c in enumerate(row[3].get("claims", []))], clients)`.
     The checkbox value is the stored index, not the position in the
     filtered list.
   - **Reviewer flag:** pass `can_attach`. It is true when `case` is set,
     `"reviewer" in user.roles` (`User.roles`, `app/main.py:34`; `require` checks the same set at `:75`), and the case is `REVIEWABLE` for the user
     (`select 1 from cases c ... where c.id=%s and {REVIEWABLE}`). Also pass
     `sources_v`, the case's `md5(public_sources::text)`, from the same
     query.
   - **Template:** when `can_attach`, wrap the claims card in
     `<form method="post" action="/review/{{ case_id }}/sources">`. Add a
     checkbox `name="n" value="{{ c.n }}"` per claim, the hidden fields
     `research_id` and `s`, and a `<button class="btn">Attach to the case
     as public sources</button>`. No JavaScript.

   → verify: `pytest tests/test_industry.py::test_research_page_offers_the_download -q`
   and the new attach tests (step 8).
   Traps:
   - `visible_claims` drops claims, so indexes must come from the stored
     list.

4. `app/review.py`, new routes after `reject` (`:261`):
   - **`POST /review/{cid}/sources`**, with
     `research_id: int = Form()`, `n: list[int] = Form([])`,
     `s: str = Form()` and `user = Depends(require("reviewer"))`:
     1. Lock the case:
        `select md5(c.public_sources::text), c.public_sources from cases c left join documents d ... where c.id=%s and {REVIEWABLE} for update of c`.
        Copy the joins from `review_detail` (`:139-142`). Return 404 if no
        row, and 409 if the md5 is not `s`.
     2. Load the research:
        `select results from research where id=%s and created_by=%s and status='done' and case_id=%s`.
        Return 404 if there is none.
     3. Copy each requested stored index: `stored[i]` for `i` in `n` within
        range. Keep only those that `visible_claims([..], clients)[0]` keeps.
        Copy only the listed keys. Add `research_id`, `attached_by=user.sub`
        and `attached_at` (now, ISO).
     4. Append with dedupe by `(url, quote)`. If the result is over 20,
        return 400 "at most 20 public sources per case".
     5. Run `update cases set public_sources=%s where id=%s` (Jsonb). Never
        touch `status`, `data` or `search_text`.
     6. Return `RedirectResponse(f"/review/{cid}", 303)`.
   - **`POST /review/{cid}/sources/remove`**, with `i: int = Form()` and
     `s: str = Form()`: the same lock and 409 rule, remove item `i` (400 if
     out of range), update and redirect.
   - Put the shared dedupe and cap in one small helper `_add_sources(old,
     new)`. `do_merge` uses it too (step 6).

   → verify: `pytest tests/test_industry.py -k attach -q`.
   Traps:
   - **Lock order:** these routes lock only the case row. They take no
     source or document lock, so the order source → documents → cases
     holds.
   - Never build the stored item from form fields.
   - Research rows are per user. Never read another user's row.

5. `app/review.py:135-164` (`review_detail`) and
   `templates/review_detail.html`, after the "Fields" card (`:42`):
   - Select `c.public_sources` and `md5(c.public_sources::text)` in the
     main query (`:139`).
   - Pass `sources = visible_claims(list, registry)[0]`, keeping each
     item's original index as `n` (as in step 3), and `sources_v`.
   - The card is "Public sources (not delivery evidence)". For each item:
     the quote in `<q>`; the publisher linked when the URL starts with
     `http://` or `https://` (copy the `research_view.html:16` rule); the
     retrieval date; the type badge; and a remove form posting `i={{ s.n }}`
     and `s`. Show the card only when the list is non-empty.

   → verify:
   `pytest tests/test_review.py tests/test_industry.py -q`.
   Traps:
   - Autoescape is on: no `| safe`.
   - The row tuple indexes `r[...]` shift if you add columns in the middle.
     Append the new columns at the end.

6. `app/review.py:389-406` (`do_merge`):
   - Read the members' `public_sources` in the same transaction, after
     `lock_members`, with
     `select public_sources from cases where id = any(%s) order by id`.
   - Fold them with `_add_sources`. Cut silently at 20 here: a merge must
     not fail on the cap.
   - Add `public_sources` to the merged-row `insert`.

   → verify: `pytest tests/test_review.py -k merge -q`.
   Traps:
   - Members are already locked by `lock_members`, so add no new lock.
   - Leave `remerge` (`:429`) and `unmerge` (`:409`) unchanged.

7. **Docs:**
   - `docs/user-guide/reviewers.md`: a new section "## Attach public
     sources" before "## Re-review" (`:129`). It covers:
     - run "Research this (public sources)" from the case;
     - tick the claims you accept and press **Attach to the case as public
       sources**;
     - remove them on the review page;
     - every download of the case shows them under "Best practice and
       public sources", marked as third-party statements;
     - the case's approval is not changed.
   - `docs/user-guide/bid-team.md:103-119`: add one paragraph. A case can
     carry attached sources, and its downloads then use the heading "Best
     practice and public sources". Research-only downloads keep "Industry
     context (public sources)".

   → verify: `pytest tests/test_user_guide.py -q`. Bold labels must match
   the template button text exactly.
   Traps: the button label in the docs must be the exact template text.

8. `tests/test_industry.py`, the new tests. Use the `rows` fixture, extended
   with a `case_id=None` argument written into `research.case_id`, and the
   `approved` fixture. Every test uses stored claims and makes no Brave
   call:
   - `test_reviewer_attaches_chosen_claims_to_the_case`
   - `test_attach_refuses_research_not_yours_not_done_or_from_another_case` (404s; plain user 403)
   - `test_attach_copies_from_the_row_not_the_form`
   - `test_attach_skips_a_claim_naming_a_protected_client`
   - `test_attach_dedupes_and_caps_at_20`
   - `test_attach_and_remove_need_the_current_list` (stale `s` → 409)
   - `test_attached_sources_appear_in_every_format` (md, docx, pptx; new heading; no `research_id`)
   - `test_attached_and_download_research_are_merged_without_duplicates`
   - `test_research_only_output_keeps_the_industry_heading`
   - `test_client_protected_after_attach_is_dropped_at_render`
   - `test_attached_sources_survive_reextraction_and_research_deletion`
   - `test_attach_does_not_reopen_an_approved_case`
   - `test_merge_unions_member_sources`, `test_remerge_and_unmerge_keep_source_lists` (in `tests/test_review.py` if the merge fixtures live there)
   - Extend `test_research_text_never_reaches_cases`: after an attach, the
     quote is not in `data` or `search_text`.

   → verify: `pytest tests/test_industry.py tests/test_review.py -q`.
   Traps:
   - Delete the research rows created (the fixture already does), and
     reset `public_sources` on shared fixtures.
   - Use made-up publishers and clients only.

## Tests

- Per step, the commands above.
- Before the PR: the full suite (`scripts/check.sh`, or
  `pytest -q` in the worktree's compose stack). The expected result is that
  everything passes. The count goes up by the new tests, from 683 on main.
- Manual, after #19 (Brave key):
  1. In the demo, run research from a case.
  2. As `reviewer`, attach two claims.
  3. Download Word and check the "Best practice and public sources"
     section.

  Never print the key.

## Rollback

- Revert the PR's code commits. The `public_sources` column is additive. It
  can stay, unused, or be dropped later with
  `alter table cases drop column if exists public_sources` in a separate,
  approved change.
- No data outside the column is changed. `data`, `search_text` and `status`
  are never written by the new routes.

## Repo traps (all steps)

- Lock order: source, then documents, then cases. The new routes lock only
  cases.
- Never read presale or confidential document content. Tests use made-up
  text only.
- The repo is public: no secrets, account ids or real client names.
- Never print or commit the Brave key.
- Research rows are private to `created_by`.
- Coders never run `docker compose up` or `down`.

## Deviations

- **Rebased onto #197** (merged as PR #201), which moved the body of
  `generate()` into a shared `build()`.
  - Step 2's changes to `generate()` now live in `build()`: reading
    `c.public_sources`, merging the attached sources with the research claims,
    and choosing the heading. The preview (`/preview`) therefore shows the
    attached sources too.
  - `build()` also returns `claims`, so `generate()` can still log
    `research_id` only when research claims were given.
- **Review fix:** attached claims are stored verbatim. `visible_claims` only
  filters them, as step 4.3 says, and render anonymises them.
