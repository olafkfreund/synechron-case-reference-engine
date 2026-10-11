---
status: draft
issue: 166
spec: spec/2026-10-10-166-accentless-names.md
---

# Plan: apply() replaces whatever blocked() finds

These decisions are copied from the approved spec (decision **B**):

- **Match on the folded text and map the hits back.** `apply()` builds
  `fold(text)` one character at a time, with an index map from each folded
  character to its original position. It runs the same patterns `blocked()`
  uses, `_pattern(fold(name))`, on that folded text. Then it replaces the
  matching original characters with the label.
- **Same names as today:** protected names and aliases, plus referenceable
  names that contain one (the current `contains` rule), longest first
  (`_by_length`).
- **Mapping a hit back to the original text:**
  - The start is `at[m.start()]`.
  - The end is `at[m.end() - 1] + 1`. It then extends over any following
    characters whose fold is empty, so a trailing combining accent goes with
    the name.
  - A hit is kept only if it overlaps no hit already kept. Longer names come
    first, so they win.
- **Replacement:** kept spans are replaced right to left, by slicing, so a
  `\` in a label is text.
- **Output:** text outside the spans is copied as written, so its case,
  accents, superscripts and fractions survive. NFKC only ever reaches the
  folded copy used for matching, so #165's bug does not come back.
- **The property:** for any text and registry, `blocked(apply(text))` is
  empty. The one exception is a label that itself contains a protected name,
  which `clients.clean` refuses.
- **`blocked()` is unchanged** and stays the fail-closed check after
  `apply()` in every caller.
- **Rejected:**
  - (A) Also trying each name's folded form against the raw text. It misses
    "Sociéte", decomposed accents, and "STRASSE" for "Straße".
  - Returning the folded text. It loses the text's own case and accents, and
    brings NFKC back.

**Merge order:** this is the last of the three `anonymise.py` changes. Start
only after #165 and #163 have merged:

- #165 adds `_no_format`, and `apply()` starts from `_no_format(text)`.
- #163 changes `_SEP` and `_pattern`. Its rules come along unchanged through
  `_pattern(fold(name))`, so apostrophe and dot variants are replaced as well.

**Size:** 4 file-editing steps in 5 files. That meets the coder threshold.

## Steps

0. Rebase. This step does not edit files.
   ```
   git fetch origin main && git rebase origin/main
   ```
   Then confirm that both earlier changes are in:
   - `grep -n "def _no_format" app/anonymise.py` finds the function from #165.
   - `grep -n "’" app/anonymise.py` finds the apostrophe class from #163 in
     `_pattern`.

   If either is missing, stop: #165 or #163 has not merged yet.

1. `app/anonymise.py`, `apply()` (on main before the rebase it is at
   `:55-68`; find it with `grep -n "def apply" app/anonymise.py`):
   - Keep the first line, `text = _no_format(text)`, from #165.
   - Keep the `pairs` / `prot` / `contains` lines (`:61-65`) as they are.
   - Replace the `for name, label in _by_length(pairs): ... sub(...)` loop and
     the `return` with:
     ```python
     folded, at = [], []  # at[j] = index in text of folded[j]; fold(ch) per char equals fold(text) (#166)
     for i, ch in enumerate(text):
         f = fold(ch)
         folded.append(f)
         at += [i] * len(f)
     folded = "".join(folded)
     spans = []
     for name, label in _by_length(pairs):
         for m in _pattern(fold(name)).finditer(folded):  # the patterns blocked() uses, on the text it sees
             if m.end() == m.start():
                 continue
             s, e = at[m.start()], at[m.end() - 1] + 1
             while e < len(text) and not fold(text[e]):  # a trailing combining accent goes with the name
                 e += 1
             if all(e <= s2 or s >= e2 for s2, e2, _ in spans):  # longer names came first and win
                 spans.append((s, e, label))
     for s, e, label in sorted(spans, reverse=True):  # right to left, by slicing: a "\" in a label is text
         text = text[:s] + label + text[e:]
     return text
     ```
   - Update the docstring's second paragraph to say this: it matches on
     `fold(text)` with `blocked()`'s patterns and replaces the matching
     original characters, so the rest keeps its case and accents, and
     `blocked()` is still the fail-closed check that must run after it.

   Verify: `tests/test_anonymise.py` passes, apart from the new tests from
   step 2, which don't exist yet.
   ```
   docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_anonymise.py
   ```

   Traps:
   - **Skip empty matches.** After #163, a `.` in a name becomes `\.?`, so a
     degenerate name could match the empty string, and `m.end() - 1` would
     then point before the start. The `continue` guards this.
   - **A hit that ends inside one character's fold** ("ß" → "ss", "½" → "1⁄2")
     maps to the whole original character. That replaces slightly more, never
     less. This is the safe direction, so don't try to split characters.
   - Only `apply()` changes. After this, `_visible` is used only by `fold()`. `blocked`, `scrub`, `has_name`, `fold` and
     `_pattern` stay as they are.
   - No new import and no cache. One `fold()` call per character is fine for
     case-sized texts.
   - There is no bind mount, so always build before you run the tests.

2. `tests/test_anonymise.py`: after #165's
   `test_apply_keeps_superscripts_and_fractions`, add:
   ```python
   def test_apply_replaces_folded_spellings():
       reg = [C("Société Zorpale", label="a bank"), C("Straße AG", label="a firm")]
       for t, label in [("Societe Zorpale", "a bank"), ("SOCIETE ZORPALE", "a bank"), ("Sociéte Zorpale", "a bank"),
                        ("STRASSE AG", "a firm")]:
           assert an.apply(f"Équipe: {t} paid 10⁶ m² ½", reg) == f"Équipe: {label} paid 10⁶ m² ½", t


   def test_apply_then_blocked_is_empty():
       reg = [C("Société Zorpale", ["Zorp"], label="a bank"), C("Straße AG", label="a firm")]
       for t in ["Société Zorpale", "Societe Zorpale", "SOCIÉTÉ ZORPALE", "Société Zorpale",
                 "Ｓｏｃｉｅｔｅ Ｚｏｒｐａｌｅ", "Soc​iété Zorpale", "Zo­rp", "Straße AG", "STRASSE AG", "strasse ag"]:
           out = an.apply(f"{t} won", reg)
           assert an.blocked(out, reg) == [] and out.endswith(" won"), t
       assert an.apply("Zorṕ won", reg) == "a bank won"  # the trailing mark goes with the name
   ```

   Verify: both tests fail on main, where "Societe Zorpale" is left in place.
   Use `check.sh` with both test ids. It swaps in main's `app/` files, runs
   the new tests and expects failures, then restores the files and runs the
   full suite.

   Traps:
   - `C(name, aliases=(), label=..., ref=False)` is the helper at the top of
     `tests/test_anonymise.py`.
   - Write accented letters literally, and decomposed or invisible ones as
     `\u` escapes, so the reader can tell them apart.

3. `tests/test_render.py`: two tests assume an accented spelling gets past
   `apply()`. It no longer does, so the 409 "withheld" path can only be
   reached by forcing `apply()` to miss.
   - **`test_protected_name_replaced_or_withheld`** (`:97`): add `monkeypatch`
     to the arguments.
     - The `evasion` case (`name.replace("o", "ö")`) now expects success:
       `r.status_code == 200`, `"Built for a UK retailer" in r.text`, and
       `evasion not in r.text`.
     - Then cover the withheld path with
       `monkeypatch.setattr(render.anonymise, "apply", lambda t, c: t)` and a
       new case whose challenge is `f"Built for {name}"`. Keep the existing
       409 and `render.WITHHELD` asserts, and the
       `generations ... count == 0` check, on that case.
     - Replace the comment `# apply() misses the accent, blocked() folds it`
       with `# #166: apply() replaces what blocked() finds`.
   - **`test_pptx_protected_name_withheld`** (`:204`): add `monkeypatch`, set
     the same `apply` identity patch, and use `value=name` instead of the
     accented spelling. Keep the asserts.

   Verify:
   ```
   docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_render.py
   ```
   passes.

   Traps:
   - `render` calls `anonymise.apply` through the module, so patching the
     module attribute reaches it. `render.py:387`'s `anonymised` flag also
     uses `apply`, and an identity patch is harmless there.
   - Check what `gen()` returns for a 200: the existing first half of the
     test reads `.text`. Match that.

4. `tests/test_research_claims.py` and `tests/test_search.py`:
   - **`test_view_shows_our_approach_only_while_visible_and_applies_protection`**
     (`test_research_claims.py:142`): add `monkeypatch`.
     - After the `update cases set data = jsonb_set(...)` with the `ö`
       spelling, assert that the page shows `"Built for a retailer"` and not
       `"[withheld]"`.
     - Then `monkeypatch.setattr(rs.anonymise, "apply", lambda t, c: t)` and
       assert `"[withheld]" in t and name not in t`, as the test does now.
     - Replace the comment `# accented evasion survives apply()` with
       `# #166: replaced, not withheld`.
   - **`tests/test_search.py:141`:** the assert `"orp" not in p["tailored"]`
     still holds, because the tailored text is now replaced rather than
     withheld. Only the comment changes, to
     `# #166: every spelling is replaced`.

   Verify:
   ```
   docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_research_claims.py tests/test_search.py
   ```
   passes.

   Traps:
   - The label in that test is "a retailer". Check the `reg` fixture in
     `tests/test_research.py` before asserting on it.

## Tests

- `test_apply_replaces_folded_spellings` and `test_apply_then_blocked_is_empty`
  fail on main and pass on the branch.
- These still pass unchanged:
  - `test_invisible_characters_inside_a_name` (#125);
  - `test_spacing_dash_accent_and_domain_variants_are_caught`;
  - `test_exact_names_with_special_case_folding`;
  - #165's `test_apply_keeps_superscripts_and_fractions`;
  - #163's punctuation tests.
- The full suite passes:
  `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`.

## Rollback

Revert the commits. `apply()` goes back to matching the text as written.
There is no data or schema change.

## Deviations

None yet.
