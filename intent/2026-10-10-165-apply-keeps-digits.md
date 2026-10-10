---
status: draft
issue: 165
author: olafkfreund
---

# Intent: apply() turns 10⁶ into 106 and m² into m2 in client-facing downloads

## Problem

#125 made `apply()` (`app/anonymise.py:61`) start with `text = _visible(text)`,
so that an invisible character inside a protected name could not hide it.
`_visible` is NFKC normalisation minus format characters (category Cf).

NFKC also rewrites compatibility characters that carry meaning:

| Written | After `apply()` |
|---|---|
| `10⁶` | `106` |
| `m²` | `m2` |
| `½` | `1⁄2` |
| `ﬁ` ligature | `fi` |
| full-width digits | ASCII digits |

`apply()` runs on every output string: `render.protect`, which feeds the
Word, PowerPoint, PDF and Markdown downloads, and the search and research
display. It does this even when the registry is empty. A case that says
"up to 10⁶ messages/day" goes to a bid as "up to 106 messages/day". I
reproduced this on main.

## Proposed outcome

- `apply()` leaves text as written, apart from replacing protected names and
  removing invisible format characters.
- Superscripts, fractions and units survive into every download.
- The #125 protection stays. A name broken by a zero-width or soft-hyphen
  character is still replaced, and `blocked()` still catches width variants,
  because it matches on `fold()`, which keeps NFKC.

## Affected users and systems

- `app/anonymise.py` (`apply`), and through it `app/render.py`,
  `app/search.py` and `app/research.py` displays.
- Bid writers, who receive the downloads.

## Constraints

- `blocked()`, `scrub()` and `fold()` keep NFKC. The fail-closed check must
  not weaken.
- Keep the #125 tests passing.
- **Overlap with #163 and #166, which also edit `app/anonymise.py`:**
  - This change touches only the first line of `apply()`, plus at most the
    `_visible(name)` call in its loop.
  - #166 rewrites how the loop matches names. If it matches on folded text
    and maps the spans back, it stops needing `_visible` in `apply()` at all.
  - Land this one first. It is the smallest, and it fixes a regression
    already on main. #166 then rebases onto it.

## Open questions

None.
