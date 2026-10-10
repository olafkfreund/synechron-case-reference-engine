---
status: draft
issue: 141
spec: spec/2026-10-10-141-research-cache-empty.md
---

# Plan: A research run with no usable pages is never reused from the cache

Approved decisions (from the spec):

- One condition is added to the cache lookup in `run` (`app/research.py`):
  `and jsonb_array_length(coalesce(results->'pages','[]'::jsonb)) > 0`.
- An empty run is still stored as `done`, with its own `skipped` list for the
  user who ran it. It is simply never a cache hit.
- No schema change or migration. Existing empty rows stop matching.
- A run with pages is reused as today, keeping its original
  `retrieved_at` and making no extra Brave call.
- Accepted cost: a query with no usable sources calls Brave on every run.

Two steps edit two files, so I implement this myself (below the coder
threshold).

## Steps

1. `app/research.py:441-443` (`run`, the `cached = conn.execute(...)` lookup):
   add the condition after `status='done'`:
   ```python
   "select results, retrieved_at from research where query=%s and status='done' and id<>%s "
   "and jsonb_array_length(coalesce(results->'pages', '[]'::jsonb)) > 0 "
   f"and retrieved_at > now() - interval '{CACHE_DAYS} days' order by id desc limit 1"
   ```
   → verify by step 2's tests.
   Traps:
   - Only the first two string pieces are plain; the `CACHE_DAYS` piece is an
     f-string. Keep `%s` placeholders out of the f-string.
   - The fragment contains no `%`, so psycopg placeholder escaping is fine.

2. `tests/test_research.py`: add `test_empty_run_is_not_reused_from_cache`
   after `test_cache_hit_within_30_days_skips_brave`, reusing its helpers
   (`web`, `cleanup`, `make_row`, `row`):
   ```python
   def test_empty_run_is_not_reused_from_cache(web, cleanup):
       q = f"research test {uuid.uuid4().hex}"
       web.results = ["https://docs.example/guide"]
       web.html("docs.example", "/robots.txt", "User-agent: *\nDisallow: /\n")
       first = make_row(q)
       rs.run(first)
       assert row(first)[0] == "done" and row(first)[2]["pages"] == []
       web.html("docs.example", "/robots.txt", "")
       web.html("docs.example", "/guide")
       second = make_row(q)
       rs.run(second)
       assert len(web.brave_calls) == 2 and len(row(second)[2]["pages"]) == 1
   ```
   → verify by
   `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_research.py -k "cache"`.
   The new test fails on main (`check.sh`).

   Traps:
   - Check what `row()` returns: the index of `results` may not be `[2]`.
     Read `row` at `tests/test_research.py:226` and adjust.
   - robots.txt is cached on the fetcher instance (`self.robots`). If one
     instance is shared across runs, the second run would reuse "disallow".
     Confirm `run` builds a new fetcher per run; if it doesn't, use a
     different path on the same host instead of changing robots.
   - There is no bind mount, so always build first.
   - Never run `docker compose up` or `down`.
   - The repo is public: use made-up names only.

## Tests

- `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`:
  the full suite passes, including `test_cache_hit_within_30_days_skips_brave`.
- The new test fails on main's `app/research.py`.

## Rollback

Revert the step commits. No data changes; empty rows simply become cache
hits again.
