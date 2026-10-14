---
status: approved
issue: 275
spec: spec/2026-10-14-275-brief-heading-flags.md
---

# Plan: Brief titles and theme headings are not flagged word by word

Worktree `../refs-engine-275`, branch `fix/275-brief-heading-flags`. It is
small (one function and its tests), so the session implements it.

## Approved decisions (from the spec)

1. **The rule.** With `heading=True`, every word of a capitalised run is
   dropped if it is ordinary (in `COMMON_STARTS`, lowercase in the
   quotes, or in `common_words()`), is not an acronym (all caps, two or
   more letters), and contains no digit. The rest is checked as today.
2. **Unchanged:** `heading=False`, #260's sentence-start rules, and the
   number and price checks.
3. **Accepted risk:** common-word product names in headings are not
   flagged.

## Steps

1. **`app/writeup.py` `check_prose`.** In the term loop, when `heading`
   is true, filter the words of `term` with the rule from decision 1
   before the date and digit filter.
   - → verify: the unit tests from the spec's verification, plus
     `pytest tests/test_writeup.py tests/test_brief*.py
     tests/test_schema.py tests/test_review*.py`.
   - Negative control: remove the filter, and the heading tests fail.
2. **Measure.** Re-run `brief.check()` over the stored briefs in the demo
   database (public data), giving flags before and after per brief.
3. **Full suite, then the Opus, Codex and agy reviews and the PR:**
   `docker compose build app && docker compose run --rm app pytest`.

## Tests

As above.

## Rollback

Revert the merge commit. Stored brief flags refresh on the next check.

## Deviations

None yet.
