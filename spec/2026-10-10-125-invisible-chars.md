---
status: draft
issue: 125
intent: intent/2026-10-10-125-invisible-chars.md
---

# Spec: An invisible character inside a protected name gets past apply() and blocked()

## Design

The approved answers are:

- **A:** drop format characters both in `fold()` and in the text `apply()`
  rewrites;
- **Scope:** drop every character in Unicode category Cf.

Everything changes in `app/anonymise.py`. No caller changes.

1. **A shared helper**, placed next to `fold()` (`:23-26`):
   ```python
   def _visible(s: str) -> str:
       """NFKC, minus format characters (Cf: soft hyphen, zero-width, BOM, bidi controls) that have no glyph (#125)."""
       return "".join(ch for ch in unicodedata.normalize("NFKC", s) if unicodedata.category(ch) != "Cf")
   ```
2. **`fold()`** starts from `_visible(s)` instead of
   `unicodedata.normalize("NFKC", s)`, so a folded string never holds a Cf
   character. `blocked()`, `has_name()`, `scrub()` and `apply()`'s `contains`
   check all fold, so all four now see "Zo<U+00AD>rp" as "zorp". This is the
   fail-closed half.
3. **`apply()`** uses `_visible()` in place of `unicodedata.normalize("NFKC", …)`
   in two places:
   - `:54`, on the text;
   - `:61`, on each name.

   The name is then rewritten rather than withheld, and the returned text
   holds no Cf characters. Every output and screen string comes from
   `apply()`: `render.protect` (`app/render.py:126`), `search.clean`,
   `industry_section`'s quote (`render.py:57`), and the research view in #117.
   So none of them ship the characters. **That is why `render.CTRL` needs no
   change.**

## Alternatives rejected

- **B: strip in `fold()` only.** `blocked()` would then withhold the output
  (409) instead of the name being rewritten. Answer 1 rejected it.
- **A hand-kept list (U+00AD, U+200B-D, U+2060, U+FEFF)** in `render.CTRL`
  and `fold()`. Answer 2 chose all of Cf. A list misses the bidi controls and
  whatever Unicode adds next.
- **Add Cf characters to `_JOIN`.** That fixes matching only, leaves them in
  the output, and doesn't cover `scrub()` or `has_name()` the same way.

## Risks

- **Meaningful Cf characters are removed from output.** The zero-width
  non-joiner (U+200C) affects shaping in Persian and some Indic scripts, and
  the zero-width joiner (U+200D) joins emoji sequences. Both are now stripped
  from every string that goes through `apply()`. Case documents are English
  bid material, so this is accepted under answer 2.
- **The `industry_section` publisher and URL keep any Cf characters.** They
  are deliberately not rewritten (`render.py:55`) and are not passed through
  `apply()`. But `blocked()` now folds them out, so a name hidden there still
  drops the claim. Nothing leaks.
- **Rebase overlap.** #118 (the `_pattern` split, `:33-37`) and #119 (the
  `_pattern` flags, `:36`) also edit `app/anonymise.py`. This change touches
  `fold()` and `apply()`'s two normalize calls, which are different lines. Any
  conflict is textual only, and whichever merges second rebases.
- **No host impact.**

## Verification

New tests in `tests/test_anonymise.py`, with a made-up protected client
"Zorp Bank" that has the alias "Zorp". For each of U+00AD, U+200B, U+2060,
U+FEFF and U+202E inside "Zorp":

- `blocked(text)` finds the name. This fails on main.
- `apply(text)` gives the label with no Cf character left. This fails on main.

In `tests/test_render.py`:

- `render.protect([{"summary": "Run for Zo­rp"}], cl)` returns the label
  and does not raise `Withheld`. This fails on main, where the name ships.

The full suite passes.
