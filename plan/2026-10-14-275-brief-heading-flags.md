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

- **Hyphenated words.** A hyphenated word is ordinary when every part is
  ("No-Code"). The measurement showed it was the last false flag.
- **Measured** on the demo's stored briefs, running branch code against
  the demo database: brief 1 went from 9 flags to 3. The 6 heading flags
  are gone, and the 3 real "has no citation" flags remain.
- **Negative control.** With the heading filter disabled, the heading
  test fails.
- **Review fixes (Opus, Codex, agy).**
  - The first word of a heading is no longer dropped just for being first.
    It is dropped only when ordinary, so "Zorbulate Integration Guide"
    flags Zorbulate.
  - A hyphenated word with an acronym part is a name ("SAP-Ready").
  - Heading names left after the ordinary words are dropped are checked
    one contiguous run at a time. "Use GitLab And ServiceNow" no longer
    becomes the unsupported "GitLab ServiceNow", and "Use IBM Cloud Pak"
    no longer becomes "IBM Pak".
  - Negative control: reverting each fix fails its test. Brief 1 still
    measures 3 flags, all "has no citation".
- **Accepted risk, wider than the spec states.** A product named with
  common words passes in a heading: "Microsoft Copilot Studio" (when
  Microsoft is quoted), Oracle, Azure, Slack, Teams. Checking those would
  bring back false flags such as "GitLab Actions". Body text is still
  checked.
