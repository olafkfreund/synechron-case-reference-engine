---
status: approved
issue: 205
author: olafkfreund
---

# Intent: Two spellings get a protected client name past the anonymiser

## Problem

I probed `app/anonymise.py` on main (702c455) with a made-up protected client,
"Zorpale Bank", whose alias is "St. James's Trust". Two spellings leak:

| Input | `scrub()` (web queries) | `apply()` and `blocked()` (outputs) |
|---|---|---|
| `Z⃝orpale Bank` (an enclosing mark inside a word) | returns `z orpale bank`, so the name goes to Brave | caught |
| `St . James's Trust` (a space before the dot) | name kept, so it goes to Brave | not replaced, and `blocked()` returns `[]` |

- **`scrub()`** folds an enclosing mark to a space only (`fold(text, " ")`).
  #187 made `blocked()` and `has_name()` try both readings, the mark as nothing
  and the mark as a space. `scrub()` still tries one, and its docstring notes
  the gap.
- **`_pattern()`** allows an optional dot straight after a word and then the
  separator (#188). A separator *before* the dot is not allowed, so
  "St . James's" matches nothing. This is the worse of the two. `blocked()` is
  the fail-closed check, so the name reaches downloads, search summaries and
  the draft model.

"St.. James's Trust" and "St James's Trust" are already caught.

## Proposed outcome

- `scrub()` removes a protected name under either reading of an enclosing mark,
  as `blocked()` does. Neither `Z⃝orpale Bank` nor `A⃝Zorpale Bank` leaves a
  name in the query.
- A name with a dot after a word ("St. James's Trust") also matches when spaces
  surround that dot ("St . James's Trust"). `apply()` replaces it, `blocked()`
  finds it, and `scrub()` removes it.
- `blocked(apply(text))` stays empty for these spellings (the #166 rule).

## Affected users and systems

- `app/anonymise.py`: `scrub`, `_pattern`.
- Every caller of `apply`, `blocked`, `has_name` and `scrub`: render, search,
  research queries and claims, and industry context.
- Bid team: names that used to leak show the label instead.

## Constraints

- Fail closed. `blocked()` must find at least everything that `apply()` replaces.
- Don't widen matching so that ordinary text matches a short name. Inner dots
  stay strict, so "U.S." never matches "us" (#163).
- No new dependency.
- Only made-up names in tests and docs, because the repo is public.

## Open questions

- Should a space before the dot be allowed only where the name itself has a dot
  after that word (my recommendation), or after any word of any name? Allowing
  it everywhere would make "Zorpale . Bank" match "Zorpale Bank". That is
  harmless for a leak check but widens the match more than this issue needs.

**Decided by the user, 2026-10-12:** only where the name itself has a dot after that word.
