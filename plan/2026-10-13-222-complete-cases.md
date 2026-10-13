---
status: approved
issue: 222
spec: spec/2026-10-13-222-complete-cases.md
---

# Plan: Approved cases are complete enough to present

Branch `feat/222-complete-cases`, worktree `../refs-engine-222`, one PR.
Line numbers are from the #212 phase B code (PR #223, at
`../refs-engine-212`), which this branch builds on. Match edits by their text.

## Approved decisions (from the spec)

- **Checklist.** `app/completeness.py` has one pure function, `checklist(case)`,
  with nine items in this order:
  1. challenge: at least 80 words
  2. solution: at least 80 words
  3. implementation: not empty
  4. configuration: not empty
  5. capabilities: at least 3 sourced
  6. technologies: at least 3 sourced
  7. results: delivered needs at least 1 sourced outcome; engagement needs a
     solution and `duration_months`
  8. period: `period.start` or `duration_months`
  9. diagram: at least 1 diagram with status `ready`
- **Thresholds live in one place:** `CHECKS` and `MINIMUM_OTHERS = 2`.
- **Section text** is `case.prose[f].text` when present. Otherwise it is the
  headline value plus the sourced point values. Unsourced items never count.
  The case and the database are read only; no model is called.
- **The minimum** is challenge and solution ok, plus at least 2 of the other
  seven items. **"Complete"** means all 9.
- **Approval below the minimum** needs a reason. Without one it returns 400.
  With one, the approval goes ahead and a row is written to
  `case_approval_overrides(id, case_id, reason ≤ 500 chars, score,
  approved_by, approved_at)`. The audit page lists the latest 100,
  autoescaped. The score is computed after the existing unsourced drops.
- **The review page** shows a "Completeness: n of 9" card with ✓/✗ text, a
  detail per item, and each ✗ linked to its section. The reason box appears
  only below the minimum.
- **Search** shows a badge on browse rows and on bid results: "Complete", or
  "Partial · n/9". It's computed from the case already loaded, so it adds no
  query.
- **Version.** `EXTRACTION_VERSION = 2` means #212 phases A and B are
  included. `ReferenceCase.extraction_version: SkipJsonSchema[int] = 1`, so
  old rows read as 1, and `build()` stamps the current version. From now on,
  every PR that changes extraction output bumps it (#212 C sets 3, D sets 4).
  When re-extraction raises a case's version, it adds the note
  "Re-extracted with richer extraction: check and approve again" and keeps
  reviewer text.
- **Bulk re-extract.** `POST /admin/reextract` is admin only, with a button on
  the Sources page showing the count. It queues at most `REEXTRACT_BATCH = 25`
  existing `extract` jobs per click (no new job kind). It only takes cases
  that:
  - have a version below the current one
  - are not rejected
  - have a document, and that document is live
  - have no extract job queued or running

  Merged parents are skipped. The redirect shows "Queued N of M older cases".

## Steps

0. **Rebase on main once #223 has merged.**
   `git -C ../refs-engine-222 fetch -q && git -C ../refs-engine-222 rebase origin/main`.
   → verify: `grep -n "prose:" app/schema.py` shows the #212 B field.
   Traps: don't start step 1 before this. The checklist reads `case.prose`.

1. **`app/completeness.py` (new) and `tests/test_completeness.py` (new).**
   - `CHECKS = {"challenge_words": 80, "solution_words": 80, "capabilities": 3, "technologies": 3}`
     and `MINIMUM_OTHERS = 2`, with the comment "tune here only".
   - `section_text(case, f)`: `case.prose[f].text` if it has text, else the
     headline value (when not unsourced) plus the values of `case.points[f]`
     that are not unsourced.
   - `checklist(case) -> list[dict(key, label, ok, detail)]` for the nine
     items. Details are plain strings, for example "42 of 80 words" or
     "1 of 3".
   - `meets_minimum(items) -> bool`.
   - `score(items) -> tuple[int, int]`.
   - Tests:
     - each item ok and not ok
     - engagement against delivered results
     - prose preferred over points
     - unsourced items not counted
     - a case with empty `prose` and `points` (an old row)
     - the minimum boundary: challenge and solution plus exactly 2 others
   → verify:
   `docker compose build app && docker compose run --rm app pytest tests/test_completeness.py`.
   Traps:
   - The module must not import `app.review` or `app.search`, because both
     import it.
   - Word counts use `len(text.split())`.

2. **Version stamp and re-extraction note**
   (`app/schema.py:107-136`, `app/extract.py:172-252`).
   - `schema.py`: add
     `extraction_version: SkipJsonSchema[int] = 1  # bump EXTRACTION_VERSION when extraction output changes`
     to `ReferenceCase`.
   - `extract.py`: add `EXTRACTION_VERSION = 2` near `MAX_CHARS`, and set
     `case.extraction_version = EXTRACTION_VERSION` in `build()` before
     `case.needs_attention = notes` (:207).
   - `extract()` (:233): if `old` exists and its `extraction_version` (default
     1) is below `EXTRACTION_VERSION`, append the note to
     `case.needs_attention`.
   - Tests in `tests/test_extract.py`:
     - `build()` stamps 2
     - re-extracting an old row (no version) adds the note and keeps reviewer
       prose
     - a current row gets no note
     - `EXTRACTION_VERSION == 2` is pinned, with a comment: "a change to
       extraction output must bump this"
   → verify:
   `docker compose build app && docker compose run --rm app pytest tests/test_extract.py tests/test_schema.py`.
   Traps:
   - `extraction_version` must not appear in `llm_schema()`, so assert that.
   - The existing upsert already resets the status and approval. Don't
     duplicate that.

3. **Approval and the review page**
   (`sql/schema.sql`, `app/review.py:199-235` and `:394-437`,
   `app/templates/review_detail.html:26`, `:43-64` and `:88`).
   - `sql/schema.sql`: `create table if not exists case_approval_overrides (id bigserial primary key, case_id bigint not null, reason text not null, score text not null, approved_by text not null, approved_at timestamptz not null default now());`.
     There is no foreign key, like `source_class_changes`, so the row
     survives case deletion.
   - `approve()`:
     - Add `reason: str = Form("")`.
     - After the drops (after the `case.period` line, before the title
       check), compute `items = checklist(case)`.
     - If `not meets_minimum(items)` and not `reason.strip()`, return 400
       "below the minimum: complete the case or give a reason".
     - After the approving `update`, if it was below the minimum, insert the
       override row with `reason.strip()[:500]`, `f"{ok}/{total}"` and
       `user.sub`.
   - `review_detail()`: pass `checks=checklist(case)` and
     `minimum=meets_minimum(...)`.
   - Template:
     - Add a "Completeness: n of 9" card right after the Case card (:26),
       with a `<ul>` of rows: "✓"/"✗", the label and the detail. A ✗ row
       links to `#fields`, `#text` or `#diagrams`.
     - Add `id="fields"`, `id="text"` and `id="diagrams"` to the three
       section cards (:43, :45, :64).
     - In the approve form (:88), add a labelled `<textarea name="reason">`
       "Why approve below the minimum?" only when `not minimum`.
   - Tests in `tests/test_review.py`:
     - below the minimum with no reason gives 400
     - with a reason, approval works and the override row exists
     - above the minimum, no reason is needed
     - the detail page shows the card and its score, escaped
   → verify:
   `docker compose build app && docker compose run --rm app pytest tests/test_review.py`.
   Traps:
   - Many existing approve tests use thin cases. Either give the shared
     approve helper a `reason`, or build fixtures that meet the minimum.
     Don't weaken the check to make old tests pass. List which you changed.
   - Score after the drops, not before.

4. **Audit page** (`app/audit.py:15-32`, `app/templates/audit.html:36-45`).
   - Query
     `select approved_at, case_id, score, reason, approved_by from case_approval_overrides order by id desc limit 100`,
     pass it as `overrides`, and add a table "Approvals below the minimum",
     styled like the class-changes table.
   - Test: a reason containing `<b>` is escaped on `/admin/audit`.
   → verify:
   `docker compose build app && docker compose run --rm app pytest tests/test_audit*.py tests/test_review.py`.
   Traps: admin only, which `audit_page` already is.

5. **Search badge** (`app/search.py:147-150`, `app/templates/search.html:29`,
   `:47` and `:67`).
   - In `view()`, add `score=score(checklist(c["case"]))`.
   - Template: a macro `completeness(s)` renders
     `<span class="badge neutral">Complete</span>` when `s[0] == s[1]`, else
     `Partial · {{ s[0] }}/{{ s[1] }}`. Use it after `basis(...)` on the
     three row kinds.
   - Test in `tests/test_search.py`: browse and bid results show "Complete" or
     "Partial · n/9".
   → verify:
   `docker compose build app && docker compose run --rm app pytest tests/test_search.py`.
   Traps: `view()` receives approved cases only, so it never leaks reviewer
   state.

6. **Bulk re-extract** (`app/sources.py:18-73`, `app/templates/sources.html`
   near :10).
   - Add `REEXTRACT_BATCH = 25`. Add `OLDER` as a shared SQL `from … where`
     fragment over `cases c join documents d on d.id = c.document_id`, with:
     - `coalesce((c.data->>'extraction_version')::int, 1) < %s`
     - `c.status <> 'rejected'`
     - `d.deleted_at is null`
     - not exists a queued or running extract job for `d.id`
   - `REEXTRACT = "insert into jobs(kind, payload) select 'extract', jsonb_build_object('document_id', d.id, 'basis', c.basis, 'basis_reason', c.data->>'basis_reason', 'checksum', d.checksum) " + OLDER + " order by c.id limit %s"`.
   - `sources_page()`: pass `older = count(*) OLDER`.
   - `POST /admin/reextract` (admin): run `REEXTRACT` and redirect to
     `/admin/sources?notice=reextract&queued=N`. Add `"reextract"` to
     `NOTICES` with the fixed text "Older cases queued for re-extraction;
     they go back to review". The template shows `queued` (an int query
     parameter) and `older` next to it.
   - Template: a form button, "Re-extract older cases ({{ older }}): cases go
     back to review", shown when `older`.
   - Tests in `tests/test_sources*.py`:
     - only older, non-rejected, live, not-already-queued cases are queued
     - a merged parent (`document_id` null) is never queued
     - capped at 25 (insert 26)
     - a plain user gets 403
     - an up-to-date case is not queued
   → verify:
   `docker compose build app && docker compose run --rm app pytest tests/test_sources*.py`.
   Traps:
   - Use the `basis` and `basis_reason` the case already has.
   - "Source marked executed" engagements keep their reason, so `extract()`'s
     #159 untick guard still applies.

7. **Full suite and a check by hand.**
   - `docker compose build app && docker compose run --rm app pytest`.
   - Rebuild the demo (`scripts/demo.sh up`), then check:
     - cases 16 and 17 show a score
     - approving one below the minimum asks for a reason, and the reason
       shows on Audit
     - "Re-extract older cases" counts the demo cases that predate #222 and
       queues at most 25
   → verify: the suite is green, and the hand check is recorded in the PR.

8. **After merge: the trap in #212's plan.** In
   `../refs-engine-212/plan/2026-10-13-212-presentable-case-studies.md`,
   phases C and D, add the trap "bump `EXTRACTION_VERSION` (C → 3, D → 4)".
   Commit it on the #212 branch.

## Deviations

- Step 4: `case_approval_overrides` is append-only for the app role
  (`sql/roles.sql` revokes update and delete), like the other audit tables.
  It has its own test.

## Tests

```bash
docker compose build app && docker compose run --rm app pytest tests/test_completeness.py tests/test_extract.py tests/test_review.py tests/test_search.py
docker compose build app && docker compose run --rm app pytest
```

## Rollback

Revert the PR's commits. `case_approval_overrides` stays as an unused table,
or drop it by hand. `extraction_version` is a stored JSON key that the old
model rejects (`extra="forbid"`), so after a revert run
`update cases set data = data - 'extraction_version';` before restarting.
