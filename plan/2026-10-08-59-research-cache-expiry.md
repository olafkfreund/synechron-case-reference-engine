---
status: approved
issue: 59
spec: spec/2026-10-08-59-research-cache-expiry.md
---

# Plan: Research cache expires 30 days after the pages were fetched

## Approved decisions (self-contained)

- `research.retrieved_at` means "when these pages were fetched".
- On a cache hit, the new row gets the cached row's `retrieved_at`, not
  `now()`. A fresh search still gets `now()`. Claims re-extraction from a
  cached row keeps the cached timestamp.
- No schema change and no data fix: rows copied before the fix expire at
  most 30 days after their copy date.
- The session model implements this itself: 1 step, 2 files.

## Steps

1. **Keep the fetch time on a cache hit.**
   - `app/research.py` line 357: select `results, retrieved_at`;
     `results = dict(cached[0])` is unchanged.
   - Line 386: `update research set status='done', results=%s,
     retrieved_at=coalesce(%s, now()) where id=%s`, passing
     `cached[1] if cached else None`.
   - `tests/test_research.py` `test_cache_hit_within_30_days_skips_brave`
     (line 257):
     - after `rs.run(first)`, backdate **only** `first` to
       `now() - interval '29 days'`;
     - after `rs.run(second)`: one Brave call, and `second`'s
       `retrieved_at` equals `first`'s;
     - age both by 2 days (`retrieved_at - interval '2 days'`), run a third:
       two Brave calls.

   → verify by:
   - running the rewritten test on today's code (stash the `app/research.py`
     change): it fails;
   - running it with the change: it passes;
   - then `docker compose build app && docker compose run --rm app pytest`.

   Traps:
   - compose has no bind mount: build before each run;
   - `cached` is `None` on a miss, so guard `cached[1]`.

## Tests

- Full suite green. The rewritten cache test fails on the old code and passes
  on the new.

## Rollback

- Revert the PR. Nothing stored changes shape.
