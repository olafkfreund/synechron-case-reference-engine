---
status: approved
issue: 163
spec: spec/2026-10-10-163-punctuation-in-names.md
---

# Plan: Protected names match their common punctuation variants

Approved decisions (spec, decision **A**: a short list):

- A comma counts as a word separator: `_SEP` gains `,`. Between a name's words the text may then have a comma, a
  space, a dash or nothing, so "Zorp Inc" matches "Zorp, Inc" and "Zorp, Inc." matches "Zorp Inc".
- In `_pattern()`, after `re.escape(w)`, each `.` becomes optional (`\.` → `\.?`), and each apostrophe variant
  (`'` U+0027, `’` U+2019, `‘` U+2018, `ʼ` U+02BC, `′` U+2032) becomes the class `['’‘ʼ′]`.
- `fold()` is unchanged. `fold()` leaves these characters alone, so the class in the pattern matches folded text as well.
- All other punctuation stays literal, so "C++ Ltd" still does not match "C Ltd".
- `apply()`, `blocked()`, `scrub()` and `has_name()` (used by `clients.clean`) all go through `_pattern`, so they
  change together. No caller is edited.

## Steps

1. `app/anonymise.py:8-13` (comment and `_SEP`) and `:40-43` (`_pattern`):
   - Set `_SEP = r"[\s,\-‐‑–—]"`. Leave `_JOIN = _SEP + "*"` as it is.
   - Add the module constant `_APOS = "'’‘ʼ′"`.
   - In `_pattern`, map each escaped word through a small helper: replace `\.` with `\.?`, then replace each
     `_APOS` character with `[` + `_APOS` + `]`. `re.escape` leaves `'` and the non-ASCII variants unescaped, so
     replace the bare characters.
   - Make these replacements only when the name contains a `\w` character (`re.search(r"\w", name)`). A name that is
     only dots, such as `"."`, would otherwise compile to `\.?`, which matches the empty string at every non-word
     position. That is the same hazard `test_dash_only_name_does_not_match_everything` guards against for `-`.
   - Add a line to the comment above `_SEP` covering the comma, the optional dot and the apostrophe variants. Keep
     the comment as terse as the existing one.

   → verify: `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_anonymise.py`

   Traps:
   - **Merge order: #165 first, then this change, then #166.** All three edit `app/anonymise.py`. #165 touches
     `_visible` and the first line of `apply()`. #166 rewrites `apply()`'s matching loop but keeps calling
     `_pattern`, so it inherits this change. Keep this diff inside `_SEP`, the new `_APOS` and `_pattern`. Do not
     touch `apply()`, `fold()` or `_visible`.
   - `apply()` builds one alternation from `_pattern(fold(n)).pattern` strings (`:63`). The class and `\.?` must
     stay valid inside an alternation, so add no anchors or groups that change the meaning of `|`.
   - The boundaries are the lookarounds `(?<!\w)` and `(?!\w)`, not `\b`. Keep them. A trailing optional dot sits
     inside them: "Zorp Inc." matches "Zorp Inc" followed by a space.
   - `test_alias_with_comma_is_one_alias` must still pass: the alias "Smith, Jones Partners X" still matches
     itself, and "Jones Partners X" on its own still does not match.

2. `tests/test_anonymise.py`: add `test_punctuation_variants_of_a_name`, placed next to
   `test_hyphenated_name_matches_spaced_joined_and_dashed`:
   - With `reg = [C("Zorpo's Bank", label="a bank")]`:
     - `an.apply("We helped Zorpo’s Bank", reg) == "We helped a bank"`
     - `an.blocked("Zorpo’s Bank", reg) == ["Zorpo's Bank"]`
     - `"zorpo" not in an.scrub("core banking for Zorpo’s Bank", reg)`
     - the reverse direction: `C("Zorpo’s Bank")` blocks `"Zorpo's Bank"`.
   - `an.apply("Zorp Inc said", [C("Zorp Inc.", label="a firm")]) == "a firm said"`
   - `an.apply("Zorp, Inc. said", [C("Zorp Inc", label="a firm")]) == "a firm. said"`. The trailing dot is outside
     the name and stays.
   - `an.blocked("C Ltd", [C("C++ Ltd")]) == []`
   - `an.blocked("plain text. more", [C(".", label="x")]) == []`. This is the dot-only guard from step 1.

   → verify: the same command as step 1. The new test fails on main (check by stashing `app/` or running
   `check.sh`) and passes on the branch.

   Traps: the test strings must contain the real U+2019 character. Write it literally or as `’`, and do not let
   an editor normalise it to `'`.

## Tests

- `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_anonymise.py`:
  everything passes, including the existing possessive, boundary, dash, #125 invisible-character and comma-alias tests.
- The full suite, `docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`, is green.
- The new test fails on main.

## Rollback

Revert the commits. Matching only narrows back to its previous behaviour. No stored data or schema is touched.

## Deviations

None yet.
