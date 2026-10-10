---
status: draft
issue: 131
spec: spec/2026-10-10-131-huge-int-reply.md
---

# Plan: A model reply with a very long number crashes extraction instead of being skipped

These decisions are copied from the approved spec:

- **The rule.** In `assemble`, a first number longer than
  `MAX_INT_CHARS = 12` characters, separators included, counts the item as
  malformed (`bad += 1`).
- **No truncation.** The regex stays `\d[\d,]*`, so the whole run is
  measured. A capped regex would truncate, and catching `ValueError` would
  still store 13–4300-digit nonsense.

**Size:** 2 file-editing steps in 2 files. That is below the coder threshold.

## Steps

1. `app/schema.py`:
   - **`:172`:** after `_INTS = (...)`, add
     `MAX_INT_CHARS = 12  # "1,000,000" fits; a longer run is a runaway reply, not a count (#131)`.
   - **`:187-189`:** change to:
     ```python
     first = re.search(r"\d[\d,]*", v)
     if not first or len(first.group()) > MAX_INT_CHARS:  # int() would raise past 4300 digits (#131)
         bad += 1
     ```

   Verify: `pytest -q tests/test_schema.py` passes, including
   `test_assemble_takes_the_first_number_only`.

   Traps: none.

2. `tests/test_schema.py`: after `test_assemble_takes_the_first_number_only`
   (`:154`), add `test_assemble_skips_runaway_numbers`. Use a local import of
   `Extraction`, `Item` and `assemble`, as that test does. Assert:
   - `team_size="1"*4301` gives `case.team_size.value is None` and a notes
     entry containing "1 malformed";
   - `"1"*13` is skipped the same way;
   - `"1,000,000"` gives `1000000`.

   Verify: the test fails on main. Stash `app/schema.py`, rebuild, run, then
   pop and rebuild.

   Traps:
   - Check the exact notes wording against `assemble`'s malformed message
     with `grep -n "malformed" app/schema.py`, and match that.

## Tests

The full suite passes, and the new test fails on main.

## Rollback

Revert the commit.
