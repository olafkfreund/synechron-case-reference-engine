---
status: approved
issue: 165
spec: spec/2026-10-10-165-apply-keeps-digits.md
---

# Plan: apply() keeps superscripts, fractions and units

These decisions are copied from the approved spec:

- **Split `_visible`.** A new `_no_format(s)` drops category Cf characters
  only (soft hyphen, zero-width, BOM, bidi controls). `_visible(s)` becomes
  `_no_format(unicodedata.normalize("NFKC", s))`. `fold()` keeps calling
  `_visible`, so `fold`, `blocked`, `scrub`, `has_name` and `resolve` do not
  change. The fail-closed check is not weakened.
- **`apply()`'s first line** becomes `text = _no_format(text)`. The output
  keeps every character as written, except invisible format characters and
  the replaced names. The name side of the loop, `_pattern(_visible(name))`,
  is unchanged.
- **Accepted gap:** a protected name written in width variants ("Ｚｏｒｐ") is
  no longer replaced by `apply()`. `blocked()` still finds it on `fold()`, so
  that output is refused (fails closed) rather than shown. #166 closes the gap.
- **Rejected:** keeping NFKC behind a whitelist of digits (the list of
  compatibility characters never ends), and reverting #125 (names hidden by
  zero-width characters would come back).
- **Merge order:** this lands first, then #163 (`_SEP`, `_pattern`), then
  #166 (the `apply()` loop, built on `_no_format`).

**Size:** 2 file-editing steps in 2 files. That is below the coder threshold.

## Steps

1. `app/anonymise.py`:
   - **`:24-27`:** replace `_visible` with two functions:
     ```python
     def _no_format(s: str) -> str:
         """Drop format characters (Cf: soft hyphen, zero-width, BOM, bidi controls) (#125). Most have no glyph;
         the few that shape text (ZWJ/ZWNJ in emoji or Indic scripts, Arabic number signs) lose that shaping."""
         return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")


     def _visible(s: str) -> str:
         """NFKC, minus format characters. For matching only: NFKC rewrites 10⁶ as 106 (#165)."""
         return _no_format(unicodedata.normalize("NFKC", s))
     ```
   - **`:60`:** `text = _visible(text)` becomes
     `text = _no_format(text)  # not NFKC: the output keeps 10⁶, m², ½ (#165)`.
   - Leave `:67` (`_pattern(_visible(name))`) and `fold()` (`:30-33`) as
     they are.

   Verify:
   ```
   docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_anonymise.py
   ```
   passes, including `test_invisible_characters_inside_a_name`.

   Traps:
   - Keep the order inside `_visible`: NFKC first, then drop Cf, as today.
     That way `fold()` is unchanged by construction.
   - `grep -rn "_visible" app/` shows its only callers are in
     `app/anonymise.py`, so nothing outside the module changes.
   - There is no bind mount, so always build before you run the tests.

2. `tests/test_anonymise.py`: after `test_invisible_characters_inside_a_name`
   (`:320-325`), add:
   ```python
   def test_apply_keeps_superscripts_and_fractions():
       t = "up to 10⁶ messages/day across 5,000 m²; saved ½"
       assert an.apply(t, []) == t
       reg = [C("Zorp Bank", label="a bank")]
       assert an.apply("Zorp Bank cut 10⁶ to 10³", reg) == "a bank cut 10⁶ to 10³"
   ```

   Verify: the test fails on main, because NFKC gives "106" and "103". Use
   `check.sh` (it swaps in main's `app/` files, runs the new test and expects
   a failure, then restores the files and runs the full suite).

   Traps:
   - Save the file as UTF-8 with the literal characters `⁶ ² ½ ³`, not
     escapes, so the test reads like the bug report.

## Tests

- `tests/test_anonymise.py::test_apply_keeps_superscripts_and_fractions` fails
  on main and passes on the branch.
- `tests/test_anonymise.py::test_invisible_characters_inside_a_name` (#125,
  all five characters) passes unchanged.
- The full suite passes:
  `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`.

## Rollback

Revert the commit. `apply()` goes back to NFKC output; no data or schema
change is involved.

## Deviations
- Step 1: `apply()` uses `_no_format(unicodedata.normalize("NFC", text))`, not `_no_format(text)`. Without any composition, decomposed (NFD) text such as "Société" no longer matched the NFC name, so `test_spacing_dash_accent_and_domain_variants_are_caught` failed. NFC only joins canonical sequences and leaves 10⁶, m², ½ as they are.
- Review, approved by the user: without NFKC on the text, a protected name in a compatibility form (full-width "Ｚｏｒｐ", the "ﬁ" ligature, "™", mathematical or circled letters, full-width hyphens) is no longer replaced by `apply()`. Callers that run `blocked()` refuse such output; `search.pick()` did not, so the name could reach the draft model's prompt. `pick()` now leaves out any candidate whose anonymised text `blocked()` flags, with `tests/test_search.py::test_pick_leaves_out_a_candidate_with_a_name_apply_missed`. #166 removes the gap by matching on folded text.
