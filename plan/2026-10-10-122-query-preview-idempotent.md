---
status: draft
issue: 122
spec: spec/2026-10-10-122-query-preview-idempotent.md
---

# Plan: Research query preview can differ from what is sent

These decisions are copied from the spec:

- `finalize` (`app/research.py:81-87`) repeats its step until the text stops
  changing. The step is: strip identifiers, scrub names, cap at `MAX_QUERY`
  (200), strip whitespace. One extra pass is not enough, for three reasons:
  - removing a name can make two numbers adjacent;
  - the cap can cut "zorp bankers" to "zorp bank";
  - removing an identifier can make two name fragments adjacent.
- The loop always ends. After the first pass the text is folded, and every
  later pass only removes characters or changes nothing. In practice it takes
  2 or 3 passes.
- The `blocked()` refusal stays after the loop. Only `finalize` changes. Its
  callers, `build_query` and `research_send`, stay as they are.

Size: 2 steps, 2 files. Below the coder handoff threshold.

## Steps

1. `app/research.py:81-87`: replace the body of `finalize` with:
   ```python
   q = text
   while True:  # each pass only removes text, so this ends; removing one thing can expose another (#122)
       nxt = anonymise.scrub(_IDENTIFIERS.sub(" ", q), clients)[:MAX_QUERY].strip()
       if nxt == q:
           break
       q = nxt
   if not q or anonymise.blocked(q, clients):
       raise ValueError("the query is empty or still contains a protected client name")
   return q
   ```
   Extend the docstring with "Run to a fixed point, so the preview is exactly
   what is sent."

   Verify with `pytest -q tests/test_research.py`: the existing tests pass.

   Traps:
   - The first comparison is between the raw input and the folded result, so
     it never ends the loop by mistake. An input that is already folded and
     clean ends in one pass.
   - Keep the `ValueError` message exactly as it is. The routes map it to the
     same 400 text.

2. `tests/test_research.py`: after `test_identifiers_stripped_from_query`
   (`:288`), add:
   - `test_finalize_is_a_fixed_point`:
     `q = rs.finalize("Basel 2023 Zorp 2024 reporting", REG)`, then assert
     `rs.finalize(q, REG) == q`. "Zorp" is the protected name in `REG`
     (`:17`).
   - `test_finalize_cap_cannot_leave_a_name_fragment`: build a question whose
     200th character falls right after "zorp" in "zorpers". For example,
     `"x " * 98 + "zorpers"` makes the cut land inside "zorpers". Change the
     padding if needed so that `[:200]` ends exactly at "zorp". Assert that
     `"zorp" not in q` and `rs.finalize(q, REG) == q`.

   Verify that both tests fail on main. For the fails-on-main check:
   `git checkout origin/main -- app/research.py`, rebuild, run the two tests,
   then `git checkout HEAD -- app/research.py` and rebuild.

   Traps:
   - `scrub()` returns folded, lowercase text. Assert on lowercase.
   - Compute the padding in the test with `len()`, so the 200-character
     arithmetic is explicit.

## Tests

`docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`
must pass in full, and the two new tests must fail on main.

## Rollback

Revert the commit. Nothing is stored or migrated.
