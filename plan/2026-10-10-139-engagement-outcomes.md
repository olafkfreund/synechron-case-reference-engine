---
status: approved
issue: 139
spec: spec/2026-10-10-139-engagement-outcomes.md
---

# Plan: A reviewer-added outcome on an engagement case shows in search as a delivered result

Approved decision B: block outcomes at review, and have search ignore them on
engagement cases too, which also covers rows approved before the fix.

- **Review page.** `rows()` adds the "add outcome" row only when
  `case.basis != "engagement"`. Existing outcome rows stay, so a reviewer can
  still remove one.
- **`edit()`.** Adding `outcomes.new` on an engagement case raises
  `ValueError`, which is the existing 400 "invalid value".
- **`approve()`.** On an engagement case, approval sets `case.outcomes = []`.
  `save()` recomputes `search_text`.
- **`search()`.** For a candidate whose `cases.basis` column is
  `engagement`, it clears `case.outcomes`. That one place covers `facts()`
  (the model prompt and the allowed numbers) and the outcomes `results()`
  shows.
- **Known gap.** Engagement cases approved before the fix keep outcome words
  in `tsv` until they are saved again, so they may still rank on them. Their
  outcomes are never shown or given to the model.

Coder handoff: yes. Four files, in four steps that edit files.

## Steps

1. **`tests/test_review.py`.** Add three tests after
   `test_add_outcome_with_foreign_quote_is_dropped_on_approval` (around line
   237). Use the `make` fixture (line 35) with `basis="engagement"`, and the
   `post()` helper (line 226).
   - `test_engagement_add_outcome_refused(make)`:
     - `post(client(R), cid, field="outcomes.new", metric="cost", value="30 percent", quote=Q_TITLE)`
       returns 400.
     - `row(cid)[1]["outcomes"]` is unchanged.
   - `test_engagement_review_page_has_no_add_outcome(make)`:
     - The page from `client(R).get(f"/review/{cid}")` doesn't contain
       `outcomes.new`.
     - The same page for a delivered `make()` does contain it, as a guard
       against a vacuous pass.
   - `test_approve_engagement_drops_outcomes(make)`:
     - `case_data()` already carries the sourced outcome "onboarding".
     - Approve with `client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)})`.
     - Assert `row(cid)[1]["outcomes"] == []` and that the case status is
       `approved`.
   - Verify: `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_review.py -k engagement`.
     All three fail on main code.
   - Traps:
     - If approval of `make(basis="engagement")` is refused for a missing
       reason, build the data the way line 88 does, with
       `basis_reason="executed contract"`.
     - Check how `rows()` renders the new-row field name before asserting on
       `outcomes.new`. Grep the delivered page for the exact string first.
     - There is no bind mount, so always build first.
     - Never run `docker compose up` or `down`.

2. **`app/review.py`.**
   - Line 103 (in `rows()`): wrap the add-outcome row as
     `if case.basis != "engagement": out.append(new("outcomes", "add outcome", ["metric", "value"]))  # contracted scope claims no results (#139)`.
   - Line 181-183 (in `edit()`, the `idx == "new"` branch): add
     `or (name == "outcomes" and case.basis == "engagement")` to the existing
     `raise ValueError` condition.
   - Line 233 (in `approve()`):
     `case.outcomes = [] if case.basis == "engagement" else [o for o in case.outcomes if not o.unsourced]  # contracted scope claims no results (#139, spec #52)`.
   - Verify: step 1's command. These must also pass:
     - the delivered add, approve and outcome tests in `tests/test_review.py`;
     - the merge tests (lines 338+), whose merged engagement case already has
       `outcomes == []`.
   - Traps:
     - Keep the remove buttons on existing outcome rows.
     - Don't change the 400 response text, which must not echo input.

3. **`tests/test_search.py`.** Add after
   `test_engagement_ranks_after_delivered_on_tie_and_is_badged` (line 163):
   - `test_engagement_outcomes_not_shown_or_given_to_model(approved, monkeypatch)`:
     - `eng = approved(); set_basis(eng, "engagement")`. The data from
       `data()` (line 15) has the outcome "onboarding 12 to 3 days".
     - Patch `sr.complete_json` to record the user prompt and return
       `sr.Picks(picks=[sr.Pick(case_id=eng, reason="", tailored="x")])`.
     - Run `sr.results(ME, "onboarding", {})`, or the `/search` page through
       `client([USER, DOCS])`.
     - Assert that `"12 to 3 days"` is in neither the recorded prompt nor the
       response.
     - Check `results()`' return shape first (`app/search.py:124`) and assert
       that the picked entry's `outcomes == []`.
   - Verify: `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_search.py`.
     The new test fails on main code.
   - Traps:
     - The summary "Onboarding fell from 12 days to 3 days." is still sent and
       shown. Assert on the outcome string "12 to 3 days", not on "3 days".

4. **`app/search.py:77-78`, `search()`.** Replace the returned list
   comprehension with:
   ```python
   cands = [dict(id=i, case=ReferenceCase.model_validate(d), label=anonymise.shown(name, label, ref, linked), basis=b, rank=r)
            for i, d, name, label, ref, linked, b, r in rows]
   for c in cands:
       if c["basis"] == "engagement":
           c["case"].outcomes = []  # contracted scope claims no results, whatever the record holds (#139)
   return cands
   ```
   - Verify: step 3's command, then the full suite.
     `test_engagement_ranks_after_delivered_on_tie_and_is_badged` must still
     pass.
   - Traps:
     - Use the `cases.basis` column (`b`), not `data.basis`.
     - Don't add a guard in `facts()` or `results()` as well.

## Tests

- `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`
  must pass in full.
- All four new tests must fail against main's `app/`.

## Rollback

Revert the branch's commits. No data or schema change; approved engagement
cases saved after the fix keep `outcomes == []`, which is correct anyway.

## Deviations
- Step 1: `test_approve_engagement_drops_outcomes` also asserts the approve returns 200 after the redirect, as the existing approve test does.
