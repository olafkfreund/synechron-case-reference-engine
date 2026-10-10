---
status: approved
issue: 139
author: olafkfreund
---

# Intent: A reviewer-added outcome on an engagement case shows in search as a delivered result

## Problem

Spec #52 says an engagement case is contracted scope, so it never claims
outcomes. That is enforced in only some places:

- `extract.build` clears extracted outcomes;
- `render.section` (`app/render.py:93-95`) drops them in outputs.

But the review page's **Add** accepts `outcomes.new` on any case. `approve`
(`app/review.py:231`) keeps sourced outcomes. Search then:

- lists them under the **Engagement (contracted scope)** badge
  (`app/search.py:136`);
- passes them to the model as allowed facts (`:45`), so a tailored summary can
  claim a result that was only contracted.

## Proposed outcome

An engagement case never shows, stores or feeds an outcome. That holds in
review, in search, in what the model is given, and in outputs.

## Affected users and systems

- Reviewers: `app/review.py` (`edit`, `approve`) and `app/templates/review*.html`.
- Search users: `app/search.py`.
- Tests: `tests/test_review.py` and `tests/test_search.py`.

## Constraints

- Server-rendered, with no JavaScript. A refused add uses the same 400 path as
  the other edit errors.
- Delivered cases are unchanged.

## Open questions

1. **How far does the fix go?**
   - **A. Stop it at the source.** `edit` refuses `outcomes.new` on an
     engagement case and the review page hides the add-outcome row for one.
     `approve` drops any outcomes on an engagement case.
   - **B. A, and search also ignores outcomes on engagement cases**, in the
     results and in `facts()`, mirroring `render.section`. That also covers
     rows approved before the fix.

   **Recommendation: B.** It is the same one-line guard render already has,
   and it covers existing rows.

**Decision (approved by olafkfreund, 2026-10-10): B.**
