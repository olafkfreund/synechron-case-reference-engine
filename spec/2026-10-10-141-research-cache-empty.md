---
status: draft
issue: 141
intent: intent/2026-10-10-141-research-cache-empty.md
---

# Spec: A research run with no usable pages is never reused from the cache

## Design

There is one change, in the cache lookup in `run` (`app/research.py`): the
`select results, retrieved_at from research where query=%s and status='done' ...`.

It adds one condition:

```sql
and jsonb_array_length(coalesce(results->'pages', '[]'::jsonb)) > 0
```

- **An empty row is skipped.** A `done` row with `pages: []` (every fetch
  or conversion failed, or Brave returned no hits) is no longer a cache hit.
  The next run searches and fetches again.
- **The empty row itself is kept.** It still stores its own `skipped` list
  for the user who ran it, so they see which domains failed.
- **No schema change and no migration.** Existing empty rows simply stop
  matching.
- **A run with pages is unchanged.** It is reused as today, with its
  original `retrieved_at`, so the 30-day window doesn't restart, and with no
  extra Brave calls.

## Alternatives rejected

- **Don't mark an empty run `done`; mark it `failed`.** That changes what
  the user sees ("No usable sources found" plus the skipped list becomes an
  error) and the status semantics other code reads. The lookup filter is
  smaller and scoped to the cache.
- **Retry the failed fetches inside the same run.** That adds time and load
  for every query, to fix only the cache problem.

## Risks

- **A query that really has no usable sources** now calls Brave on every run,
  where it used to call once per 30 days. Each call is still one search per
  user action, and it is rate-limited and retried as today. That cost is
  accepted: the key is only used when a run is asked for.

## Verification

New test in `tests/test_research.py`, which must fail on main:

- `tests/test_research.py::test_empty_run_is_not_reused_from_cache`
  - **First run:** Brave returns one hit and its fetch fails (robots
    disallowed). Its row is `done` with `pages == []`.
  - **Second run:** the same query, after the page is made fetchable. Brave
    is called a second time (`len(web.brave_calls) == 2`) and the row has
    one page.

The existing `test_cache_hit_within_30_days_skips_brave` still passes, so a
run with pages is still reused with one Brave call. The full suite stays
green.
