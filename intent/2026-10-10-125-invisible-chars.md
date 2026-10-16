---
status: approved
issue: 125
author: olafkfreund
---

# Intent: An invisible character inside a protected name gets past apply() and blocked()

## Problem

Text extracted from PDFs and Word documents can carry invisible formatting
characters: the soft hyphen U+00AD, the zero-width U+200B-D, U+2060 and
U+FEFF. `schema._FOLD` (`app/schema.py:12-15`) already drops two of them when
it compares quotes, and `tests/test_schema.py:61` treats them as normal
artifacts. So a value can carry one and still pass the quote check.

The anonymiser doesn't handle them:

- `fold()` (`app/anonymise.py:23-26`) keeps them, because they are not
  combining marks.
- `_JOIN` (`:12`) doesn't match them, because they are not `\s`.

So with a made-up protected "Zorp", the text `Zo<U+00AD>rp` is not rewritten
by `apply()` and not caught by `blocked()`. `render.protect` passes it.
`render.CTRL` (`app/render.py:29`) strips only C0 control characters, so the
character ships, and every viewer shows "Zorp". This is a leak past the
fail-closed check. It reaches every output format, the search summaries
(`search.clean`) and the research view.

## Proposed outcome

- A protected name with invisible characters inside it is treated exactly as
  the name without them, by `apply()`, `blocked()`, `has_name()` and `scrub()`.
- Generated outputs never ship those invisible characters.

## Affected users and systems

- `app/anonymise.py` (`fold`, and the text `apply()` works on), and through it
  render, search, research and the registry check.
- `app/render.py` (`CTRL`).
- Tests: `tests/test_anonymise.py` and `tests/test_render*.py`.

## Constraints

- Fail closed. `blocked()` must catch the name even if `apply()` misses it.
- One shared rule in `anonymise`, not a patch in each caller.
- Visible text is unchanged: these characters have no glyph.
- Use only made-up names.

## Open questions

1. **Where are the invisibles removed?**
   - **A. In both places.** `fold()` drops every Unicode format character
     (category Cf), so `blocked()` catches the name everywhere. `apply()` drops
     the same characters from the text before it rewrites, so the name is
     replaced rather than the output being withheld.
   - **B. Only in `fold()`.** Then `blocked()` withholds the output (409)
     instead of the name being rewritten.

   **Recommendation: A.** No leak, and no refused downloads.
2. **Drop all of category Cf, or a named list?** Cf also holds the
   bidirectional controls (U+202A-E, U+2066-9), which can reorder how a name
   is displayed. **Recommendation: all of Cf.** A list would have to be kept up
   to date by hand.

## Approved answers

1. A: drop format characters in fold() and in the text apply() rewrites.
2. All of Unicode category Cf.
