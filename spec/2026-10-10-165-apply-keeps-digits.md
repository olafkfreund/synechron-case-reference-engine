---
status: draft
issue: 165
intent: intent/2026-10-10-165-apply-keeps-digits.md
---

# Spec: apply() keeps superscripts, fractions and units

## Design

`app/anonymise.py`:

- Split `_visible` into two steps. A new `_no_format(s)` drops category Cf
  characters only. `_visible(s)` becomes `_no_format(unicodedata.normalize("NFKC", s))`,
  so `fold()` and everything built on it (`blocked`, `scrub`, `has_name`,
  `resolve`) is unchanged.
- `apply()`: its first line becomes `text = _no_format(text)`. The output keeps
  every character as written except invisible format characters and the
  replaced names. The name side (`_pattern(_visible(name))`) is unchanged.

**Merge order with the other `anonymise.py` changes:** this lands first. It
touches only `_visible` and the first line of `apply()`. #163 changes `_SEP` and
`_pattern`; #166 rewrites the matching loop in `apply()` and starts from
`_no_format(text)`.

## Alternatives rejected

- **Keep NFKC in `apply()` and protect digits with a whitelist:** NFKC rewrites
  many compatibility characters (ligatures, circled numbers, ℃, ™); a list
  never ends.
- **Revert #125:** that brings back names hidden by zero-width or soft-hyphen
  characters.

## Risks

- A protected name written in width variants ("Ｚｏｒｐ") is no longer replaced
  by `apply()`. `blocked()` still matches it on `fold()`, so that download is
  refused (fails closed) instead of shown. #166 (match on the folded text and
  map back) makes `apply()` replace it again; until then this is the accepted
  gap.
- No change to search or research inputs: those already go through `fold()`.

## Verification

- New `tests/test_anonymise.py::test_apply_keeps_superscripts_and_fractions`:
  `an.apply("up to 10⁶ messages/day across 5,000 m²; saved ½", [])` returns
  the text unchanged, and with a registry `[C("Zorp Bank", label="a bank")]`,
  `an.apply("Zorp Bank cut 10⁶ to 10³", reg) == "a bank cut 10⁶ to 10³"`.
  Fails on main (NFKC gives "106" and "103").
- `tests/test_anonymise.py::test_invisible_characters_inside_a_name` (#125,
  all five characters) still passes unchanged.
- Full suite green.
