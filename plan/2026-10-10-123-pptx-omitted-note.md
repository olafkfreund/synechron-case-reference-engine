---
status: draft
issue: 123
spec: spec/2026-10-10-123-pptx-omitted-note.md
---

# Plan: PowerPoint output drops the "statements omitted" note on the industry slide

These decisions are copied from the spec:

- `slide_statements` (`app/render.py:74-78`) adds `ind["note"]` after the
  `SLIDE_ITEMS` slice, so the 5-item limit never cuts the note off. Both
  `to_pptx` branches fill their box from this one function:
  - the dedicated layout's `Statements` (`:202`);
  - the fallback's `Outcomes` (`:207`).

  Nothing else calls it, so both layouts get the note.
- Reuse the note text built by `industry_section` (`:66-67`). No new wording,
  and no change to the master template or the placeholders.

Size: 2 steps, 2 files. Below the coder handoff threshold.

## Steps

1. `app/render.py:74-78`: append `+ ([ind["note"]] if ind["note"] else [])`
   after `[:SLIDE_ITEMS]`. Extend the docstring with "The omitted-statements
   note (if any) comes last, as in Word and Markdown (#123)."

   Verify by `grep -n "slide_statements" app/render.py`, which shows the
   definition and two calls. Then run `pytest -q tests/test_industry.py`.

   Traps:
   - `industry_section` always sets `"note"`, as `""` when nothing was
     omitted, so plain `ind["note"]` is safe. Don't use `.get`, which would
     hide a missing key.

2. `tests/test_industry.py`:
   - **Dedicated layout:** after
     `test_a_claim_naming_a_protected_client_is_replaced_or_dropped_and_counted`
     (`:116`), add `test_pptx_industry_slide_shows_the_omitted_note`. Copy its
     setup: one claim that names `reg`, one normal claim, and a case. Generate
     `fmt="pptx"`, then read the industry slide with the file's `slides`
     helper, as in `test_pptx_industry_slide_keeps_the_quote_next_to_the_phrase`
     (`:103`). Assert that:
     - the last line of `Statements` is
       "1 public statement omitted: it named a protected client";
     - the first line is the normal claim.
   - **Fallback layout:** extend
     `test_fallback_to_case_layout_without_industry_layout` (`:151`), or add a
     sibling test with its setup, so it also includes a claim that names
     `reg`. Assert that the note is the last line of `Outcomes`.

   Verify that both fail on main, using the checkout of `app/render.py` from
   `origin/main` as in #122.

   Traps:
   - The fallback test monkeypatches the master path to a copy in
     `tmp_path`. Keep that setup.
   - `reg` is a fixture: take its name from the fixture's return value, as the
     existing test at `:116` does.

## Tests

The full suite must pass, and the two new assertions must fail on main.

## Rollback

Revert the commit.
