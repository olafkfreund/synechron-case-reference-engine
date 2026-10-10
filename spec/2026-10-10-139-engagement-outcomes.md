---
status: approved
issue: 139
intent: intent/2026-10-10-139-engagement-outcomes.md
---

# Spec: A reviewer-added outcome on an engagement case shows in search as a delivered result

## Design

The approved decision is **B**: stop outcomes at the source, and have search
ignore them on engagement cases too, which also covers rows already approved.
Each guard reads the case's basis, the same test `render.section`
(`app/render.py`) already uses.

**Review (`app/review.py`):**

- **`rows()`** adds the `outcomes.new` ("add outcome") row only when
  `case.basis != "engagement"`. Existing outcome rows stay, so a reviewer can
  still remove one from an older record.
- **`edit()`**, in the `name in LISTS and idx == "new"` branch, raises
  `ValueError` when `name == "outcomes" and case.basis == "engagement"`. That
  gives the same `400 "invalid value"` as any other refused add, with no input
  echoed back.
- **`approve()`** replaces
  `case.outcomes = [o for o in case.outcomes if not o.unsourced]` with:

  ```python
  # contracted scope claims no results (#139, spec #52)
  case.outcomes = [] if case.basis == "engagement" else [o for o in case.outcomes if not o.unsourced]
  ```

  The approved data then holds none. `save()` recomputes `search_text`, so
  approved engagement cases stop indexing outcome words.

**Search (`app/search.py` `search()`).** When building each candidate, a row
whose `cases.basis` column is `engagement` gets its outcomes cleared:

```python
case = ReferenceCase.model_validate(d)
if b == "engagement":
    case.outcomes = []  # contracted scope claims no results, whatever the record holds (#139)
```

This one place covers everything downstream:
- `facts()`, which builds the model prompt and the allowed numbers in `pick()`;
- the `outcomes` list that `results()` shows under the Engagement badge.

The `cases.basis` column is used rather than `data.basis`, because the column
is what search already returns and badges by.

## Alternatives rejected

- **A. Source only (review and approve).** Cases approved before the fix would
  keep showing outcomes in search until someone re-approves them. Rejected at
  intent approval.
- **A guard inside `facts()` and `results()` separately.** That is two guards
  where one at candidate construction does the job, and a future caller could
  miss one.
- **A data migration to strip outcomes from existing engagement rows.** The
  search guard makes it unnecessary, and the repo has no migration tooling for
  `data`.

## Risks

- **Ranking.** Engagement cases approved before the fix keep outcome words in
  `tsv` until they are re-saved, so they may still rank on those words. Their
  outcomes are never shown or given to the model.
- **The add-outcome row disappearing** could surprise a reviewer used to it.
  The engagement badge and note on the review page already say why.
- **A merged case** is built with `basis="engagement"` and `outcomes=[]`
  (`app/review.py` merge), so it is unchanged.

## Verification

New tests that must fail on main:

- `test_engagement_add_outcome_refused` in `tests/test_review.py`: posting
  `outcomes.new` on an engagement case returns 400, and the stored outcomes are
  unchanged.
- `test_engagement_review_page_has_no_add_outcome` in `tests/test_review.py`:
  the review page of an engagement case has no `outcomes.new` field.
- `test_approve_engagement_drops_outcomes` in `tests/test_review.py`: an
  engagement case whose data has a sourced outcome has `outcomes == []` after
  approval.
- `test_engagement_outcomes_not_shown_or_given_to_model` in
  `tests/test_search.py`:
  - an approved case has a sourced outcome and the column `basis='engagement'`;
  - the picked result lists no outcomes;
  - the prompt the model fake receives doesn't contain the outcome text.

Existing tests that must keep passing:
- `test_engagement_ranks_after_delivered_on_tie_and_is_badged`;
- the delivered-case add, approve and outcome tests in `tests/test_review.py`.

The full suite must pass.
