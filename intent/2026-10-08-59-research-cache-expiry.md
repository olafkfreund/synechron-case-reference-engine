---
status: approved
issue: 59
author: olafkfreund
---

# Intent: Research cache expires 30 days after the pages were fetched

## Problem

Industry research (`app/research.py` `run()`) reuses a recent result for the
same query instead of calling Brave and fetching the pages again. A result
is reused only if it is less than 30 days old (`CACHE_DAYS`), going by the
`research.retrieved_at` column.

On a cache hit, the old result is copied into the new research row, but the
row is stamped `retrieved_at = now()` (line 386). The copy then looks fresh,
and the next request for the same query reuses the copy. As long as someone
asks the same question at least once every 30 days, pages fetched once are
served indefinitely. That breaks plan step 19 of #1 (a 30-day cache). Found
in the #36 review: a 29-day-old result, reused once and then aged 2 days, was
still served at day 31.

What it does not affect: each page keeps its own `retrieved_at`, so the
dates shown in the research view and the outputs stay true.

The test (`tests/test_research.py` `test_cache_hit_within_30_days_skips_brave`)
backdates both rows together, which hides the bug.

## Proposed outcome

- A cached result is served only while the pages it holds are less than 30
  days old, however often it is reused.
- After 30 days the next request for that query searches and fetches again.
- The test fails on today's code and passes after the fix.

## Affected users and systems

- `app/research.py` `run()` and `tests/test_research.py`.
- Bid team users of industry research: after the fix they get fresh pages at
  least every 30 days, at the cost of one Brave call and the page fetches.

## Constraints

- No schema change. The dates shown to users stay as they are.
- Must not re-fetch within the 30 days (keep the cost saving).
- Claims re-extraction on an old cached row (no claims or a failure note)
  keeps working as today.

## Open questions

None. The fix is to keep the cached row's `retrieved_at` on a cache hit,
unless the spec finds a reason not to.
