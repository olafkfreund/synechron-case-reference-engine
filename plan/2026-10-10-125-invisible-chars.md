---
status: approved
issue: 125
spec: spec/2026-10-10-125-invisible-chars.md
---

# Plan: An invisible character inside a protected name gets past apply() and blocked()

These decisions are copied from the spec:

- **A new helper** `_visible(s)`: NFKC, then drop every Unicode category Cf
  character. That covers the soft hyphen, the zero-width characters, the BOM
  and the bidi controls.
- **`fold()` starts from `_visible(s)`.** `blocked()`, `has_name()`,
  `scrub()` and `apply()`'s `contains` check all fold, so they fail closed on
  a hidden name.
- **`apply()` uses `_visible()`** in place of `normalize("NFKC", …)`, on the
  text (`:54`) and on each name (`:61`). So the name is rewritten rather than
  withheld, and `apply()`'s output holds no Cf characters.
- **`render.CTRL` is unchanged.** Every output and screen string comes from
  `apply()`. The industry publisher and URL are never rewritten, so they can
  still hold a Cf character, but `blocked()` now catches a name hidden in them.
- **Only `app/anonymise.py` changes in app code.**

Size: 2 steps, 3 files (`app/anonymise.py`, `tests/test_anonymise.py`,
`tests/test_render.py`). That meets the coder handoff threshold on the file
count.

## Steps

1. `app/anonymise.py`:
   - **Above `fold()` (`:23`).** Add:
     ```python
     def _visible(s: str) -> str:
         """NFKC, minus format characters (Cf: soft hyphen, zero-width, BOM, bidi controls) that have no glyph (#125)."""
         return "".join(ch for ch in unicodedata.normalize("NFKC", s) if unicodedata.category(ch) != "Cf")
     ```
   - **`:25`.** `s = unicodedata.normalize("NFKD", _visible(s).casefold())`.
   - **`:54`.** `text = _visible(text)`.
   - **`:61`.** `_pattern(_visible(name))`.

   Verify by `grep -n 'normalize("NFKC"' app/anonymise.py`, which shows only
   the line inside `_visible`. Then run `pytest -q tests/test_anonymise.py`.

   Traps:
   - `fold()` still applies NFKD and drops combining marks after the
     casefold. Keep that.
   - #118 and #119 edit `_pattern` (`:33-37`), and #118 also edits `_JOIN`
     (`:12`). Those are different lines, so rebase conflicts are unlikely. If
     one hits, keep both changes.
   - Don't strip Cf in `_pattern`. Names reach it already passed through
     `_visible` or `fold`.

2. Tests:
   - **`tests/test_anonymise.py`.** Add
     `test_invisible_characters_inside_a_name`, parametrised over U+00AD,
     U+200B, U+2060, U+FEFF and U+202E, with `reg = [C("Zorp Bank", ["Zorp"], label="a bank")]`.
     For `t = f"Run for Zo{ch}rp"`:
     - `an.blocked(t, reg) == ["Zorp"]`;
     - `an.apply(t, reg) == "Run for a bank"`.
   - **`tests/test_render.py`.** Add
     `test_invisible_character_in_name_is_rewritten_not_shipped`, with a
     client dict `{"id": 1, "name": "Zorp Bank", "aliases": ["Zorp"], "anonymised_label": "a bank", "referenceable": False}`.
     Assert that `render.protect([{"summary": "Run for Zo­rp"}], cl)[0]["summary"] == "Run for a bank"`,
     with no `Withheld` raised.

   Verify that both fail on main.

   Traps:
   - Write the characters in tests as `\u` escapes, never as literals.
     Invisible literals are unreviewable.
   - Check `render.protect`'s dict shape in its existing callers in
     `tests/test_render.py` (`:227`) before writing the test.

## Tests

The full suite must pass, and the new tests must fail on main.

## Rollback

Revert the commit.

## Deviations

- **Review fix (nit):** the `_visible` docstring now says that some Cf
  characters do affect display (ZWJ/ZWNJ in emoji or Indic scripts, and
  Arabic number signs), and that dropping them loses that shaping. Our
  corpus is English bid text, so this is accepted.
