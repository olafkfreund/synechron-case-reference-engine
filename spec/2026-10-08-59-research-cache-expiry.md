---
status: approved
issue: 59
intent: intent/2026-10-08-59-research-cache-expiry.md
---

# Spec: Research cache expires 30 days after the pages were fetched

## Design

`research.retrieved_at` means "when these pages were fetched", not "when
this row finished". One change in `run()` (`app/research.py` lines 353–390):

- The cache lookup (line 357) also selects the cached row's `retrieved_at`.
- On a cache hit, the final update writes that value: `retrieved_at = %s`
  with the cached timestamp. On a fresh search it stays `now()`. That's one
  parameter, `cached[1] if cached else None`, and `coalesce(%s, now())` in
  the existing update at line 386.
- Claims re-extraction from a cached row (no claims, or a failure note) also
  keeps the cached timestamp, because the pages are the same.

The 30-day window therefore counts from the original fetch. Copies of a copy
carry the same timestamp, so a chain of reuses ends 30 days after the first
fetch.

## Alternatives rejected

- **Compute the age from the oldest page's `retrieved_at` inside the JSON.**
  That's the same information, but it needs a JSON query in the lookup and
  breaks for a result with no pages.
- **A new `fetched_at` column.** That's a schema change for what the existing
  column already means.
- **Never copy, link to the cached row instead.** That changes how the
  research view and the outputs read results, for no gain.

## Risks

- **Rows already copied** before the fix carry a fresher timestamp than their
  pages. They expire at most 30 days after their own copy date, then
  everything is correct. No data fix is needed: the dates users see are
  per page and were always true.
- **Hosts:** the worker only. No schema change. Rollback is a revert.

## Verification

- `docker compose build app && docker compose run --rm app pytest` is green.
- `test_cache_hit_within_30_days_skips_brave` is rewritten so it fails on
  today's code:
  - run a first search, backdate **only that row** to 29 days;
  - a second request hits the cache (no new Brave call), and its row's
    `retrieved_at` equals the first's;
  - age both rows by 2 more days (the first to 31): a third request calls
    Brave again.

  On today's code, the second row would be stamped `now()`, so the third
  request would still hit the cache and the test fails.
- The existing claims re-extraction test still passes.
