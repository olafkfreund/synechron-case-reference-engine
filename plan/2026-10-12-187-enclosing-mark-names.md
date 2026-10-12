---
status: draft
issue: 187
spec: spec/2026-10-12-187-enclosing-mark-names.md
---

# Plan: A protected name with an enclosing mark or a trademark sign leaks

## Approved decisions

- **The leak.** With the protected client "Zorpale Bank", nine spellings leak
  on main: `apply()` leaves them as they are and `blocked()` returns `[]`.
  - "Zorpale™ Bank", "Zorpale® Bank", "Zorpale Bank™", "Zorpale Bank℠",
    "ZorpaleBank™"
  - "Z⃝orpale Bank", "Zorpale⃝ Bank", "Zorpale⃣ Bank", "Zorpale҈ Bank"
- **The fix is in one place, `fold`.** Before NFKC, `fold` drops ™ ℠ ® (a new
  constant `_SIGNS`) and every enclosing mark (`unicodedata.category(ch) == "Me"`).
  - It must drop them before NFKC, because NFKC rewrites ™ as "TM" and ℠ as
    "SM".
  - All matchers go through `fold` (`blocked`, `has_name`, `scrub`, `_key`,
    `resolve`, `_replace_once`), so `apply()` and `blocked()` stay in step.
  - Nothing outside `app/anonymise.py` calls `fold` or `_visible`.
- **`_replace_once` doesn't change.** A dropped character folds to "", so a
  match maps back across it. The loop that extends a span over trailing
  characters folding to "" takes a trailing sign or mark too: "Zorpale Bank™"
  becomes the label. Text outside a replaced name keeps its signs, so "Acme™
  Cloud" stays as it is.
- **Decided by the user:** '·', '/', '_' and '.' between words do **not**
  become separators. "Zorpale/Bank" stays unmatched and needs an alias.
- **Rejected:**
  - Adding the signs to `_SEP`: it misses a mark inside a word and misses "tm"
    after NFKC.
  - Stripping the signs in `apply()`: it changes the visible text, and
    `blocked()` would stay blind.
  - Dropping all symbols (So): too wide.
  - Treating Me as combining: `combining()` gives 0 for Me.
- **Accepted effects:**
  - "Zorpale™" and "Zorpale" are now the same registry key, so the second is
    refused as a duplicate.
  - "Zorpale Bank®" used to give "X®" and now gives "X".
  - `scrub` output loses the signs.

## Steps

1. `tests/test_anonymise.py`, at the end of the file after
   `test_apply_then_blocked_is_empty` (`:384-393`). Add two tests that use the
   existing `C()` helper (`:10-11`) and `an`:
   ```python
   @pytest.mark.parametrize("t", ["Zorpale™ Bank", "Zorpale® Bank", "Zorpale Bank™", "Zorpale Bank℠", "ZorpaleBank™",
                                  "Z⃝orpale Bank", "Zorpale⃝ Bank", "Zorpale⃣ Bank", "Zorpale҈ Bank"])
   def test_trademark_signs_and_enclosing_marks_in_a_name(t):
       reg = [C("Zorpale Bank", label="a bank")]
       assert an.blocked(t, reg) == ["Zorpale Bank"]
       out = an.apply(f"{t} won", reg)
       assert out == "a bank won" and an.blocked(out, reg) == []  # a trailing sign goes with the name


   def test_signs_outside_a_name_stay():
       reg = [C("Zorpale Bank", label="a bank")]
       assert an.apply("Acme™ Cloud and Zorpale Bank® win", reg) == "Acme™ Cloud and a bank win"
       assert an.apply("Zorpale/Bank", reg) == "Zorpale/Bank" and an.blocked("Zorpale/Bank", reg) == []  # decided: not a separator
   ```
   **Verify** by rebuilding first, then running
   `docker compose build -q app && docker compose run --rm app pytest -q -p no:cacheprovider tests/test_anonymise.py -k "trademark or signs_outside"`.
   On main, expect 10 failures: nine from the parametrized test and one from
   the second test, whose first assert gets "...a bank® win".

   **Traps:**
   - There is no bind mount, so always run `docker compose build -q app`
     before tests.
   - Use only made-up names, because the repo is public.
   - Write the enclosing marks as `\u` escapes so they survive editors.

2. `app/anonymise.py`:
   - At `:16`, after `_APOS`, add
     `_SIGNS = "™℠®"  # dropped before NFKC, which turns ™ into "TM" (#187)`.
   - In `fold` (`:38-41`), add a first line before the NFKD line:
     `s = "".join(ch for ch in s if ch not in _SIGNS and unicodedata.category(ch) != "Me")  # enclosing marks have combining class 0`.
   - Update the docstring to mention the signs and enclosing marks.
   - Change nothing else. `_replace_once`, `apply` and `blocked` stay as they
     are.

   **Verify** with the same command as step 1: the 10 tests pass. Then run
   `tests/test_anonymise.py` in full, which must stay green, in particular
   `test_apply_keeps_superscripts_and_fractions` and
   `test_apply_then_blocked_is_empty`.

   **Traps:**
   - Rebuild the image before testing.
   - Don't drop the signs in `_visible` or `_no_format`. `apply()` uses
     `_no_format` on the visible text, and "Acme™ Cloud" must keep its ™.
   - Don't add the signs to `_SEP`.

## Tests

`docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`

The full suite must pass: 653 tests before this change, plus 10 new ones.

## Rollback

Revert the merge commit. The change is pure Python, with no schema, data or
infra change. Matching goes back to treating the signs and marks as part of
the word, so the nine spellings leak again until someone adds an alias.
