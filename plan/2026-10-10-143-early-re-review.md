---
status: approved
issue: 143
spec: spec/2026-10-10-143-early-re-review.md
---

# Plan: Open approved cases for re-review 30 days before they expire

The approved decisions (intent decision B, plus the spec):

- **When a case is open.** `OPEN` in `app/review.py` also covers approved
  cases due within 30 days:
  `(c.status = 'extracted' or (c.status = 'approved' and c.review_due <= now() + interval '30 days'))`.
  `load()` and `review_detail` use it, so `edit`, `approve` and `reject` open
  together. The 30-day window is the one the "Due for re-review within 30 days"
  list already uses.
- **The queue does not list a case twice.** A new `QUEUE` constant keeps
  today's rule: `(c.status = 'extracted' or (c.status = 'approved' and c.review_due < now()))`.
  `review_list`'s `found` query uses it. The due-soon list is unchanged.
- **Re-approving.** `approve` is unchanged. It runs every check, sets
  `status='approved'` and a new `review_due = now() + 12 months`, and the case
  stays in search throughout.
- **An edit withdraws an approval that has not yet expired.** After `save()`,
  `edit` resets an approved, in-date case to `extracted`, with `approved_by`,
  `approved_at` and `review_due` set to null. It leaves search and goes back to
  the main queue until it is approved again. An expired case keeps today's
  behaviour, and reject is unchanged.
- **The reviewer is told.** On an open, approved case the detail page shows:
  "This case is approved and in search until {{ due }}. Approving it again
  renews it for 12 months. Saving any change takes it out of search until you
  approve it again."
- **The guide.** `docs/user-guide/reviewers.md`, Re-review, step 2 is
  reworded to match.
- **Accepted risk.** Due-soon approved engagements also show the merge form
  30 days earlier.
- **Rejected:** guide only, edits that stay live, a draft copy column, and
  approve or reject with no edits.

Five steps edit four files (`app/review.py`, `review_detail.html`,
`reviewers.md`, `tests/test_review.py`). That is over the threshold, so the
work goes to the `coder` agent.

## Steps

1. **`app/review.py:29`, `OPEN`, and `:118-120`, `review_list`.**
   - Change `OPEN` to the decided expression above.
   - Add `QUEUE` directly below it, with today's expression.
   - Change `{OPEN}` to `{QUEUE}` in the `found` query on line 120 only.
   - Verify with step 5's `test_due_soon_case_not_listed_twice` and the
     existing `test_list_shows_extracted_and_expired_only`.
   - Traps: `load()` on line 54 and `review_detail` on line 137 must keep
     `{OPEN}`. Don't replace every use.

2. **`app/review.py:212-213`, `edit`.** Between `save(conn, cid, case)` and the
   `return`, still inside `with db.connect() as conn:`, add:
   ```python
   conn.execute("update cases set status='extracted', approved_by=null, approved_at=null, review_due=null "
                "where id=%s and status='approved' and review_due > now()", (cid,))  # unreviewed edits never go live (#143)
   ```
   - Verify with `test_edit_on_due_soon_case_withdraws_approval`.
   - Traps:
     - It must be in the same transaction as `save()`, after the row lock
       `load()` takes.
     - Don't touch `approve` or `reject`.

3. **`app/review.py:137` and `:153-157`, `review_detail`.**
   - Append `, c.review_due` to the select. It becomes `r[9]`.
   - Pass `due=r[9].strftime("%Y-%m-%d") if r and r[9] else None` to `page(...)`.
   - **`app/templates/review_detail.html`, after the `page-head` div (line 21).**
     Add:
     `{% if reviewable and status == "approved" %}<p class="muted" role="note">This case is approved and in search until {{ due }}. Approving it again renews it for 12 months. Saving any change takes it out of search until you approve it again.</p>{% endif %}`
   - Verify with `test_due_soon_case_can_be_reapproved_and_stays_searchable`,
     which checks the note text.
   - Traps:
     - `r` may be None before the 404 check, so guard as shown.
     - No JavaScript.
     - The expired case also has `status == "approved"` and is open. Its
       `review_due` is in the past, and the note would claim it is in search.
       Show the note only when the date is in the future: pass
       `due=... if r and r[9] and r[9] > datetime.now(timezone.utc) else None`
       and test `{% if reviewable and status == "approved" and due %}`. Import
       `datetime` and `timezone` from `datetime` if they aren't already
       imported.

4. **`docs/user-guide/reviewers.md:137`, Re-review step 2.** Replace
   "2. Open each case and review it as above." with "2. Open each case. Approve
   it as it is to renew it for 12 months; it stays in search. If you edit it,
   it leaves search until you approve it again."
   - Verify by `grep -n "renew it for 12 months" docs/user-guide/reviewers.md`.
   - Traps: none.

5. **`tests/test_review.py`.**
   - **Move two existing tests to 60 days.**
     - Line 133, `test_approving_decided_case_is_409`:
       `make("approved", "30 days")` becomes `make("approved", "60 days")`.
     - Line 334, `test_no_add_or_remove_buttons_when_not_reviewable`: the
       same change.
   - **Add three tests after `test_list_shows_extracted_and_expired_only`:**
     - `test_due_soon_case_can_be_reapproved_and_stays_searchable(make)`:
       - `cid = make("approved", "10 days")`.
       - The page `client(R).get(f"/review/{cid}").text` contains
         `f'action="/review/{cid}/approve"'` and "Approving it again renews
         it".
       - A POST to approve with `{"v": ver(cid)}` returns 303.
       - `row(cid)[0] == "approved"` and `row(cid)[4]` is True.
       - The case is in search before and after:
         `f'name="case_id" value="{cid}"' in client([USER, DOCS]).post("/search", data={"industry": "Aerospace"}).text`.
     - `test_edit_on_due_soon_case_withdraws_approval(make)`:
       - `cid = make("approved", "10 days")`.
       - A POST to edit with
         `{"field": "industry", "value": "Banking", "v": ver(cid)}` returns
         303.
       - `row(cid)[0] == "extracted"` and `row(cid)[5]` is False, meaning
         `review_due` is null.
       - `f'/review/{cid}"'` is in the `/review` text.
       - The search check above is now False.
     - `test_due_soon_case_not_listed_twice(make)`:
       - `cid = make("approved", "10 days")`.
       - In `t = client(R).get("/review").text`, `t.count(f'/review/{cid}"') == 1`,
         and its index is after `t.index("Due for re-review within 30 days")`.
   - Verify with
     `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_review.py`.
     All three new tests fail on main's code.
   - Traps:
     - `make`'s `due` is an interval string.
     - The search check needs the `user` role, which is why it uses
       `client([USER, DOCS])` and not `R`.
     - The fixture's industry must match "Aerospace", as in `FIND["search"]`.
       Reuse that check rather than inventing one.
     - Use made-up data only.

## Tests

- **Fail on main:**
  - `tests/test_review.py::test_due_soon_case_can_be_reapproved_and_stays_searchable`
  - `::test_edit_on_due_soon_case_withdraws_approval`
  - `::test_due_soon_case_not_listed_twice`
- **Pass unchanged:**
  - `test_list_shows_extracted_and_expired_only`
  - `tests/test_hardening.py::test_review_page_lists_approvals_expiring_within_30_days`
- **Full suite:**
  `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`
  is green.

## Traps (all steps)

- There is no bind mount, so build before every test run.
- Never run `docker compose up` or `down`.
- The coder cannot commit. The session model commits each step as
  `(plan #143 step K)`.
- The repo is public, so use made-up names only.
- Any deviation goes into a `## Deviations` section of this plan, in the same
  commit as the code.

## Rollback

Revert the step commits. No migration is involved. Cases that an edit withdrew
stay `extracted` and need approving again, which is the decided behaviour.

## Deviations
- Step 5: the POSTs that expect 303 pass `follow_redirects=False` (the test client follows redirects by default).
- Step 5: the re-approve test builds its case with a sourced industry. The default fixture's industry is unsourced, `approve` strips it, and the case would leave the "Aerospace" search for that reason, not this change.
- Step 5: a small `in_search(cid)` helper is shared by the new tests.
- Steps 2 and 3 share `app/review.py` and are one commit.
