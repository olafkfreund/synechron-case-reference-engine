---
status: draft
issue: 166
author: olafkfreund
---

# Intent: A protected name written without its accents is not replaced, so the whole download is refused

## Problem

`apply()` (`app/anonymise.py:55-68`) replaces protected names by matching each
name, case-insensitively, against the text as written. It does not fold
accents. `blocked()` (`:71-75`) matches on `fold()`, which removes case,
accents and width, and it fails closed.

The two disagree on accents. With the protected client "Société Zorpale" in
the registry, a document that says "Societe Zorpale" or "SOCIETE ZORPALE" (or
"Strasse Zorp" for "Straße Zorp") gets through `apply()` unchanged. `blocked()`
then finds the name. I reproduced this on main.

- `render.generate` turns the hit into a 409 "output withheld". Every
  download of that case is refused.
- Search shows "[withheld]" and an empty summary.
- The reviewer can't fix it. An admin has to add each unaccented spelling as
  an alias.

Nothing leaks, because the check fails closed. But the case is unusable for
an ordinary spelling difference.

## Proposed outcome

- Anything `blocked()` would find, `apply()` replaces first. For any text and
  registry, `blocked(apply(text))` is empty, unless a label itself contains a
  protected name, which `clients.clean` already refuses.
- Downloads of cases with unaccented, upper-case or "ss" spellings of a
  protected name succeed, with the label in place of the name.
- The rest of the text keeps its own case and accents. Only the name is
  replaced.

## Affected users and systems

- `app/anonymise.py` (`apply`), and through it every render, search and
  research display.
- Bid writers and reviewers.

## Constraints

- `blocked()` stays the fail-closed check, unchanged.
- No new dependency.
- **Overlap with #163 and #165, which also edit `app/anonymise.py`:**
  - #165 lands first. It changes only the first line of `apply()`, so that
    NFKC no longer rewrites the output.
  - #163 changes `_pattern()` and `fold()`, which govern punctuation.
  - If `apply()` matches on `fold()`'s output, #163's punctuation folding
    reaches `apply()` and `blocked()` together. So land #163 before or after
    this one, but keep its change inside `fold()` and `_pattern()`.

## Open questions

1. **How does `apply()` find the accentless spellings?**
   - **A. Also try each name's folded form** as a second pattern against the
     text as written (with `re.I`). This catches "Societe Zorpale" and "SOCIETE
     ZORPALE". It misses spellings that only fold equal, such as mixed accents
     ("Sociéte") or decomposed characters, so some downloads are still
     refused.
   - **B. Match on `fold(text)` and map each hit back to the original
     characters.** `fold` is built character by character with an index map,
     and the hits are replaced in the original text from right to left. This
     matches exactly what `blocked()` matches, so the two can't disagree
     again. It is about 15 lines.

   **Recommendation: B.** It turns "apply replaces whatever blocked finds"
   into a property the tests can assert, rather than a list of spellings to
   keep chasing.
