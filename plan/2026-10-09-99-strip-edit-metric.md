---
status: draft
issue: 99
spec: spec/2026-10-09-99-strip-edit-metric.md
---

# Plan: Trim every edited text field in the reviewer edit

Approved decisions, copied from the spec:

- **One change, in `edit`** (`app/review.py:160-211`): trim the five text form inputs (`value`, `metric`,
  `start`, `end`, `quote`) once, at the top of the function body, before `with db.connect()` (`:165`).
  `None` stays `None` (absent field = leave unchanged, `:178`, `:196`, `:203`).
- Every existing branch then sees trimmed text, so a whitespace-only input takes the existing empty
  paths: summary `""`, text scalar `None`, edited outcome metric/value `""`, edited list item `None`,
  period `None`, quote `""`. Adds (`:181-183`) stay refused with 400 when blank; a valid add is stored
  trimmed.
- This also closes the sourced gap: a scalar `"  "` with a document quote no longer passes `check()`
  and survives approve; it is stored as `None`.
- **Not changed:** `check()` (`app/extract.py`), the Pydantic models (`app/schema.py`), extraction,
  templates, stored data (no migration). The redundant `.strip()` calls at `:181` stay.
- Only `str.strip()`: inner text unchanged. Quote matching goes through `_norm`, which already drops
  outer whitespace, so trimming cannot change a match.

Two files change, so the session model implements it (below the coder hand-off threshold).

## Steps

1. `app/review.py:164` (after the `# unsourced is never read` comment, before `with db.connect()`):
   add one line
   ```python
   value, metric, start, end, quote = (s.strip() if s is not None else None for s in (value, metric, start, end, quote))
   ```
   No other line in `edit` changes.
   → verify by `git diff app/review.py` showing a single added line.
   Traps: keep `None` as `None`, never `""`, or an absent `quote` would wipe `source_quote` (`:203`)
   and an absent `value` would clear a scalar (`:178`). Do not touch `field` or `action`.

2. `tests/test_review.py`, after `test_add_needs_value_and_quote` (`:247-255`), add three tests using
   the existing `make`, `client(R)`, `post`, `row`, `ver` helpers:
   - `test_whitespace_metric_stored_empty`: `post(c, cid, field="outcomes.0", metric="   ", value=" 30% ",
     quote=Q_TITLE)` → 303. Stored `outcomes[0]` has `metric == ""`, `value == "30%"`.
     `section(ReferenceCase.model_validate(data), "Client")` (import `section` from `app.render`):
     the Outcomes list bullets are `["30%"]` (no `": 30%"`). `"30%"` is in `search_text`.
   - `test_whitespace_value_not_sourced_and_dropped_on_approval`: `post(c, cid, field="industry",
     value="   ", quote=Q_REGION)` → 303. Stored `industry.value is None` and `unsourced is False`.
     Approve (`c.post(f"/review/{cid}/approve", data={"v": ver(cid)})`), then `industry.value is None`.
   - `test_padded_add_is_trimmed`: `post(c, cid, field="outcomes.new", metric=" Revenue ",
     value=" 30% ", quote=Q_TITLE)` → 303; last outcome has `metric == "Revenue"`, `value == "30%"`.
   → verify by running the three tests on main's `app/review.py` first (`git stash` step 1): the first
   two must fail; then with step 1 they pass.
   Traps: no bind mount, so rebuild the image before every run. `make()`'s default case is
   `delivered` basis, so Outcomes render (an `engagement` case would drop them). Approve redirects:
   assert on `row()`, not the response body.

## Tests

```
docker compose build app && docker compose run --rm app timeout 900 pytest tests/test_review.py
docker compose build app && docker compose run --rm app timeout 900 pytest
```

Expected: `test_review.py` all pass including the three new tests; the full suite passes with the
count on main plus 3. Existing `test_add_needs_value_and_quote` still returns 400 for blank adds.

## Rollback

Revert the commit (`git revert <sha>`). No data, schema or config change to undo; values trimmed
while the fix was live stay trimmed, which is harmless.
